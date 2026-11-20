ENTRY(_start)
SECTIONS
{
    . = 0x02000000;
    .head : { *(.head) *(.head.*) . = ALIGN(4); __compress_start = .; }
    .text : { *(.text*) }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) }
    . = ALIGN(4);
    __autoload_start = .;
    .itcm 0x01FF8000 : AT(__autoload_start) { *(.itcm) . = ALIGN(4); }
    __itcm_size = SIZEOF(.itcm);
    . = __autoload_start + __itcm_size;
    __autoload_list_start = .;
    .autoload_list __autoload_list_start : AT(__autoload_list_start) { LONG(0x01FF8000) LONG(__itcm_size) LONG(0) }
    __autoload_list_end = .;
    . = ALIGN(4);
    __bss_start = .;
    .bss __bss_start (NOLOAD) : AT(__bss_start) { *(.bss*) *(COMMON) }
    . = ALIGN(4);
    __bss_end = .;
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.attributes) *(.comment) }
}
