ENTRY(bios_main)
SECTIONS
{
    . = 0x02000000;
    .text : { *(.text*) *(.glue_7) *(.glue_7t) }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) }
    .bss : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.ARM.attributes) }
}
