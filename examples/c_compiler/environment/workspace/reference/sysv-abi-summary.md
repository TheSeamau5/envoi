# System V AMD64 ABI Summary

Target platform: Linux x86_64 using the System V AMD64 ABI.

## Register Usage

| Purpose | Registers |
| --- | --- |
| Integer/pointer arguments | `rdi`, `rsi`, `rdx`, `rcx`, `r8`, `r9` |
| Return value | `rax` |
| Caller-saved (scratch) | `rax`, `rcx`, `rdx`, `rsi`, `rdi`, `r8`, `r9`, `r10`, `r11` |
| Callee-saved (must preserve) | `rbx`, `rbp`, `r12`, `r13`, `r14`, `r15` |
| Stack pointer | `rsp` |
| Frame pointer (optional) | `rbp` |

Arguments 7 and beyond are passed on the stack.

## Stack Alignment

- `rsp` must be 16-byte aligned at the point of every `call`.
- The `call` instruction pushes an 8-byte return address, so the callee starts with `rsp % 16 == 8`.
- Typical consequence: after `push %rbp`, the stack is aligned again. If you then reserve locals with `subq $N, %rsp`, choose `N` so the next `call` still happens with 16-byte alignment.
- If you push an odd number of extra 8-byte values, add another `subq $8, %rsp` or equivalent padding before calling out.
- Misalignment often crashes `printf` and other libc routines that use SSE instructions.

## Common Prologue / Epilogue Patterns

With a frame pointer:

```asm
pushq %rbp
movq %rsp, %rbp
subq $N, %rsp
...
movq %rbp, %rsp
popq %rbp
ret
```

Without a frame pointer:

```asm
subq $N, %rsp
...
addq $N, %rsp
ret
```

## Variadic Functions

- Before calling a variadic function, set `al` to the number of vector-register arguments used for floating-point values.
- For common integer-only `printf` calls, zero it with `xorl %eax, %eax` before `call printf@PLT`.

## Locals and Parameters

- With a frame pointer, locals are usually at negative offsets from `rbp`: `-4(%rbp)`, `-8(%rbp)`, and so on.
- Stack-passed arguments are at positive offsets from `rbp` in a framed function.
- Without a frame pointer, locals are addressed relative to `rsp`, which changes whenever you push or pop.

## Practical Rules

- Save and restore every callee-saved register you use.
- Do not assume caller-saved registers survive a function call.
- Keep stack alignment correct before every `call`.
- Use the codegen examples in `codegen-examples/` as concrete patterns.
