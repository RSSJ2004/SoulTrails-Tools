// Exporta todas las funciones del programa actual a <outdir>/functions.jsonl
// Cada línea: {"name","addr","size","block","c"}  (c = pseudo-C del decompilador)
// Uso headless: -postScript ExportFunctions.java <outdir>
//@category SpiritTracks

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.lang.Register;
import java.math.BigInteger;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.nio.charset.StandardCharsets;

public class ExportFunctions extends GhidraScript {

    private static String esc(String s) {
        StringBuilder b = new StringBuilder(s.length() + 16);
        for (char c : s.toCharArray()) {
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        return b.toString();
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        File outDir = new File(args.length > 0 ? args[0] : ".");
        outDir.mkdirs();
        File out = new File(outDir, "functions.jsonl");

        DecompInterface dec = new DecompInterface();
        dec.openProgram(currentProgram);
        int timeout = 60;

        int total = currentProgram.getFunctionManager().getFunctionCount();
        monitor.initialize(total);
        int n = 0;
        try (Writer w = new OutputStreamWriter(new FileOutputStream(out), StandardCharsets.UTF_8)) {
            FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
            while (it.hasNext() && !monitor.isCancelled()) {
                Function f = it.next();
                if (f.isExternal() || f.isThunk()) continue;
                MemoryBlock blk = currentProgram.getMemory().getBlock(f.getEntryPoint());
                boolean thumb = false;
                Register tm = currentProgram.getRegister("TMode");
                if (tm != null) {
                    BigInteger v = currentProgram.getProgramContext().getValue(tm, f.getEntryPoint(), false);
                    thumb = v != null && v.intValue() == 1;
                }
                String c = "";
                DecompileResults r = dec.decompileFunction(f, timeout, monitor);
                if (r != null && r.decompileCompleted() && r.getDecompiledFunction() != null) {
                    c = r.getDecompiledFunction().getC();
                }
                w.write("{\"name\":\"" + esc(f.getName()) + "\",\"addr\":\"0x"
                        + Long.toHexString(f.getEntryPoint().getOffset()) + "\",\"size\":"
                        + f.getBody().getNumAddresses() + ",\"block\":\""
                        + esc(blk != null ? blk.getName() : "") + "\",\"thumb\":" + thumb + ",\"c\":\"" + esc(c) + "\"}\n");
                n++;
                monitor.incrementProgress(1);
            }
        }
        dec.dispose();
        println("Exportadas " + n + " funciones a " + out.getAbsolutePath());
    }
}
