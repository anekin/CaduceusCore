/* Golden ring entry byte output — matches firmware cmd_entry_t layout.
 *
 * This small C program produces the authoritative byte layout for known
 * cmd_entry_t values. The Python test reads the output and verifies that
 * the Python `<III` packing of the first three uint32 fields matches the C
 * `cmd_entry_t` struct.
 *
 * Build: gcc -o golden_ring_entry sim/tests/golden_ring_entry.c
 * Run:   ./golden_ring_entry
 */

#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef struct __attribute__((packed, aligned(4))) {
    uint32_t opcode;
    uint32_t desc_addr;
    uint32_t flags;
    uint32_t _pad[5];  /* align to 32B */
} cmd_entry_t;

int main(void) {
    cmd_entry_t entries[] = {
        {0x00000001, 0x80F00000, 0x00000003, {0}},
        {0xFFFFFFFF, 0xDEADBEEF, 0xCAFEBABE, {0}},
        {0x00000000, 0x00000000, 0x00000000, {0}},
        {0x00000042, 0x01234567, 0x89ABCDEF, {0}},
        {0x7FFFFFFF, 0x80000000, 0x00000001, {0}},
    };
    const int n = (int)(sizeof(entries) / sizeof(entries[0]));

    /* Emit the first three uint32_t fields (12 bytes) in C struct order.
     * This is the authoritative reference for the ring entry payload. */
    for (int i = 0; i < n; i++) {
        cmd_entry_t *e = &entries[i];
        printf("// entry[%d]: opcode=0x%08x desc_addr=0x%08x flags=0x%08x\n",
               i, e->opcode, e->desc_addr, e->flags);

        /* Dump the first 12 bytes (3 × uint32 LE) as a Python bytes literal */
        uint8_t *b = (uint8_t *)e;
        printf("b\"");
        for (int j = 0; j < 12; j++) {
            printf("\\x%02x", b[j]);
        }
        printf("\",\n");
    }

    return 0;
}
