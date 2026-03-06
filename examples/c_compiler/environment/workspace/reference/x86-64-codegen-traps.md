# x86-64 Codegen Traps

These are the mistakes that repeatedly break otherwise close-to-correct compilers.

## Stack Alignment Before `call`

- `rsp` must be 16-byte aligned at the `call` instruction.
- The callee enters with `rsp % 16 == 8` because the return address was pushed.
- If you `pushq %rbp` first, you are aligned again.
- If you then reserve locals, keep the next `call` aligned.
- Bad alignment often crashes `printf`.

## `idiv` Needs `cdq` or `cqo`

- Before `idivl`, run `cdq`.
- Before `idivq`, run `cqo`.
- Otherwise `edx` or `rdx` contains garbage and signed division breaks, especially for negative values.

## Comparison Results Live in Flags

- `cmp` sets flags. It does not write a `0` or `1` result into a general-purpose register.
- Common pattern:

```asm
cmpl %esi, %edi
setl %al
movzbl %al, %eax
```

## 32-bit Writes Zero-Extend

- Writing to a 32-bit register clears the upper 32 bits of the corresponding 64-bit register.
- `movl $-1, %eax` produces `0x00000000ffffffff` in `rax`, not all 64 bits set.

## Globals Use RIP-Relative Addressing

- On x86_64 ELF, globals are usually accessed with RIP-relative addressing.
- Example: `movl counter(%rip), %eax`
- Do not emit absolute addresses unless you know why you need them.

## Callee-Saved Registers Must Be Restored

- If you use `rbx`, `rbp`, or `r12`-`r15`, save and restore them.
- Forgetting this causes bugs far away from the real error site.

## `lea` Does Not Read Memory

- `leaq -8(%rbp), %rax` computes an address.
- It does not load the value stored at that address.
- Use `lea` for address-of and indexed address calculation.

## String Literals Belong in `.rodata`

- Emit them once, null-terminate them, and reference them with RIP-relative addresses.
- Typical pattern: `leaq .LC0(%rip), %rdi`

## Function-call Boundary

- Your compiler should emit assembly.
- It is fine to hand that assembly to `as` and then link the resulting objects with `gcc` or `ld`.
- It is not fine to let gcc compile the C for you.
