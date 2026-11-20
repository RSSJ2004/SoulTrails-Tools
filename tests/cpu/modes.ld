ENTRY(qemu_start)
SECTIONS
{
    . = 0x02000000;
    .text : { *(.text*) }
    .data : { *(.data*) }
    /DISCARD/ : { *(.ARM.attributes) }
}
