// Servicios que el núcleo de melonDS pide al programa que lo usa (Platform.h): archivos, hilos,
// sincronización, tiempo y guardado. Mínimos para el port: sin red, cámara ni accesorios.
// Este archivo forma parte del port híbrido con melonDS (GPL-3.0, ver LICENSE).
#include <chrono>
#include <condition_variable>
#include <cstdarg>
#include <cstdio>
#include <mutex>
#include <string>
#include <thread>

#include "Platform.h"
#include "SPI_Firmware.h"

namespace melonDS {
namespace Platform {

void st_melon_write_save(const u8* data, u32 len);   // st_melon.cpp

void SignalStop(StopReason reason, void*) { std::fprintf(stderr, "[melonDS] parada (%d)\n", (int)reason); }

struct FileHandle { std::FILE* f; };

static const char* fmode(FileMode m) {
    bool text = m & FileMode::Text;
    if ((m & FileMode::Read) && (m & FileMode::Write)) {
        if (m & FileMode::Preserve) return text ? "r+" : "r+b";
        return text ? "w+" : "w+b";
    }
    if (m & FileMode::Append) return text ? "a" : "ab";
    if (m & FileMode::Write) return text ? "w" : "wb";
    return text ? "r" : "rb";
}

std::string GetLocalFilePath(const std::string& filename) { return filename; }
FileHandle* OpenFile(const std::string& path, FileMode mode) {
    if ((mode & FileMode::NoCreate) && !FileExists(path)) return nullptr;
    std::FILE* f = std::fopen(path.c_str(), fmode(mode));
    if (!f) return nullptr;
    return new FileHandle{f};
}
FileHandle* OpenLocalFile(const std::string& path, FileMode mode) { return OpenFile(path, mode); }
bool FileExists(const std::string& name) {
    std::FILE* f = std::fopen(name.c_str(), "rb");
    if (!f) return false;
    std::fclose(f);
    return true;
}
bool LocalFileExists(const std::string& name) { return FileExists(name); }
bool CheckFileWritable(const std::string& filepath) {
    std::FILE* f = std::fopen(filepath.c_str(), "ab");
    if (!f) return false;
    std::fclose(f);
    return true;
}
bool CheckLocalFileWritable(const std::string& filepath) { return CheckFileWritable(filepath); }
bool CloseFile(FileHandle* file) {
    bool ok = std::fclose(file->f) == 0;
    delete file;
    return ok;
}
bool IsEndOfFile(FileHandle* file) { return std::feof(file->f) != 0; }
bool FileReadLine(char* str, int count, FileHandle* file) { return std::fgets(str, count, file->f) != nullptr; }
bool FileSeek(FileHandle* file, s64 offset, FileSeekOrigin origin) {
    int o = origin == FileSeekOrigin::Start ? SEEK_SET : origin == FileSeekOrigin::Current ? SEEK_CUR : SEEK_END;
#ifdef _WIN32
    return _fseeki64(file->f, offset, o) == 0;
#else
    return fseeko(file->f, offset, o) == 0;
#endif
}
void FileRewind(FileHandle* file) { std::rewind(file->f); }
u64 FileRead(void* data, u64 size, u64 count, FileHandle* file) { return std::fread(data, size, count, file->f); }
bool FileFlush(FileHandle* file) { return std::fflush(file->f) == 0; }
u64 FileWrite(const void* data, u64 size, u64 count, FileHandle* file) { return std::fwrite(data, size, count, file->f); }
u64 FileWriteFormatted(FileHandle* file, const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = std::vfprintf(file->f, fmt, ap);
    va_end(ap);
    return n < 0 ? 0 : (u64)n;
}
u64 FileLength(FileHandle* file) {
    long pos = std::ftell(file->f);
    std::fseek(file->f, 0, SEEK_END);
    long len = std::ftell(file->f);
    std::fseek(file->f, pos, SEEK_SET);
    return len < 0 ? 0 : (u64)len;
}

void Log(LogLevel level, const char* fmt, ...) {
    if (level == LogLevel::Debug) return;
    va_list ap;
    va_start(ap, fmt);
    std::fprintf(stderr, "[melonDS] ");
    std::vfprintf(stderr, fmt, ap);
    va_end(ap);
}

struct Thread { std::thread t; };
Thread* Thread_Create(std::function<void()> func) { return new Thread{std::thread(std::move(func))}; }
void Thread_Free(Thread* thread) {
    if (thread->t.joinable()) thread->t.detach();
    delete thread;
}
void Thread_Wait(Thread* thread) {
    if (thread->t.joinable()) thread->t.join();
}

struct Semaphore {
    std::mutex m;
    std::condition_variable cv;
    int count = 0;
};
Semaphore* Semaphore_Create() { return new Semaphore(); }
void Semaphore_Free(Semaphore* s) { delete s; }
void Semaphore_Reset(Semaphore* s) {
    std::lock_guard<std::mutex> l(s->m);
    s->count = 0;
}
void Semaphore_Wait(Semaphore* s) {
    std::unique_lock<std::mutex> l(s->m);
    s->cv.wait(l, [s] { return s->count > 0; });
    s->count--;
}
bool Semaphore_TryWait(Semaphore* s, int timeout_ms) {
    std::unique_lock<std::mutex> l(s->m);
    if (!s->cv.wait_for(l, std::chrono::milliseconds(timeout_ms), [s] { return s->count > 0; })) return false;
    s->count--;
    return true;
}
void Semaphore_Post(Semaphore* s, int count) {
    {
        std::lock_guard<std::mutex> l(s->m);
        s->count += count;
    }
    s->cv.notify_all();
}

struct Mutex { std::mutex m; };
Mutex* Mutex_Create() { return new Mutex(); }
void Mutex_Free(Mutex* m) { delete m; }
void Mutex_Lock(Mutex* m) { m->m.lock(); }
void Mutex_Unlock(Mutex* m) { m->m.unlock(); }
bool Mutex_TryLock(Mutex* m) { return m->m.try_lock(); }

void Sleep(u64 usecs) { std::this_thread::sleep_for(std::chrono::microseconds(usecs)); }
u64 GetMSCount() {
    return (u64)std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count();
}
u64 GetUSCount() {
    return (u64)std::chrono::duration_cast<std::chrono::microseconds>(
               std::chrono::steady_clock::now().time_since_epoch()).count();
}

void WriteNDSSave(const u8* savedata, u32 savelen, u32, u32, void*) { st_melon_write_save(savedata, savelen); }
void WriteGBASave(const u8*, u32, u32, u32, void*) {}
void WriteFirmware(const Firmware&, u32, u32, void*) {}
void WriteDateTime(int, int, int, int, int, int, void*) {}

void MP_Begin(void*) {}
void MP_End(void*) {}
int MP_SendPacket(u8*, int, u64, void*) { return 0; }
int MP_RecvPacket(u8*, u64*, void*) { return 0; }
int MP_SendCmd(u8*, int, u64, void*) { return 0; }
int MP_SendReply(u8*, int, u64, u16, void*) { return 0; }
int MP_SendAck(u8*, int, u64, void*) { return 0; }
int MP_RecvHostPacket(u8*, u64*, void*) { return 0; }
u16 MP_RecvReplies(u8*, u64, u16, void*) { return 0; }
int Net_SendPacket(u8*, int, void*) { return 0; }
int Net_RecvPacket(u8*, void*) { return 0; }

void Camera_Start(int, void*) {}
void Camera_Stop(int, void*) {}
void Camera_CaptureFrame(int, u32*, int, int, bool, void*) {}
bool Addon_KeyDown(KeyType, void*) { return false; }
void Addon_RumbleStart(u32, void*) {}
void Addon_RumbleStop(void*) {}
float Addon_MotionQuery(MotionQueryType, void*) { return 0.0f; }

DynamicLibrary* DynamicLibrary_Load(const char*) { return nullptr; }
void DynamicLibrary_Unload(DynamicLibrary*) {}
void* DynamicLibrary_LoadFunction(DynamicLibrary*, const char*) { return nullptr; }

}  // namespace Platform
}  // namespace melonDS
