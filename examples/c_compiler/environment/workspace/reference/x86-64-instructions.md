# x86-64 Instructions You Actually Need

Linux x86_64 gcc emits AT&T syntax by default:

- Source comes first, destination second.
- `movl $42, %eax` means "move 42 into eax".
- Size suffixes: `b` = 8-bit, `w` = 16-bit, `l` = 32-bit, `q` = 64-bit.

## Data Movement

| Instruction | AT&T example | What it does |
| --- | --- | --- |
| `mov` | `movl -4(%rbp), %eax` | Copy data |
| `movsx` | `movsbq -1(%rbp), %rax` | Sign-extend into a larger register |
| `movzx` | `movzbl %al, %eax` | Zero-extend into a larger register |
| `lea` | `leaq -16(%rbp), %rax` | Compute an address without loading memory |
| `push` | `pushq %rbp` | Store 8 bytes on the stack and decrement `rsp` |
| `pop` | `popq %rbp` | Load 8 bytes from the stack and increment `rsp` |

## Arithmetic

| Instruction | AT&T example | What it does |
| --- | --- | --- |
| `add` | `addl %edx, %eax` | Integer addition |
| `sub` | `subl %edx, %eax` | Integer subtraction |
| `imul` | `imull %edx, %eax` | Signed multiplication |
| `neg` | `negl %eax` | Unary minus |
| `cdq` | `cdq` | Sign-extend `eax` into `edx:eax` before 32-bit `idiv` |
| `cqo` | `cqo` | Sign-extend `rax` into `rdx:rax` before 64-bit `idiv` |
| `idiv` | `idivl %ecx` | Signed division; quotient in `eax`, remainder in `edx` |

## Bitwise and Shifts

| Instruction | AT&T example | What it does |
| --- | --- | --- |
| `and` | `andl %edx, %eax` | Bitwise AND |
| `or` | `orl %edx, %eax` | Bitwise OR |
| `xor` | `xorl %eax, %eax` | Bitwise XOR; common zeroing idiom |
| `not` | `notl %eax` | Bitwise NOT |
| `shl` | `shll $2, %eax` | Shift left |
| `shr` | `shrl $1, %eax` | Logical shift right |
| `sar` | `sarl $1, %eax` | Arithmetic shift right, preserving sign |

## Comparisons and Jumps

| Instruction | AT&T example | What it does |
| --- | --- | --- |
| `cmp` | `cmpl %edx, %eax` | Set flags from subtraction without storing the result |
| `test` | `testl %eax, %eax` | AND two operands and set flags from the result |
| `je` / `jne` | `je .L1` | Jump if equal / not equal |
| `jl` / `jle` | `jl .L1` | Signed less-than / less-or-equal |
| `jg` / `jge` | `jg .L1` | Signed greater-than / greater-or-equal |
| `jb` / `jbe` | `jb .L1` | Unsigned below / below-or-equal |
| `ja` / `jae` | `ja .L1` | Unsigned above / above-or-equal |
| `jmp` | `jmp .L1` | Unconditional jump |
| `sete` | `sete %al` | Write 1 if equal, else 0 |
| `setne` | `setne %al` | Write 1 if not equal, else 0 |
| `setl` / `setle` | `setl %al` | Signed less-than / less-or-equal result |
| `setg` / `setge` | `setg %al` | Signed greater-than / greater-or-equal result |

After a `set*`, use `movzbl %al, %eax` if you need a full 32-bit `0` or `1`.

## Calls and Returns

| Instruction | AT&T example | What it does |
| --- | --- | --- |
| `call` | `call add` | Push return address and jump to callee |
| `ret` | `ret` | Pop return address and jump back |

## Stack Pointer Adjustment

| Instruction | AT&T example | What it does |
| --- | --- | --- |
| Reserve stack space | `subq $32, %rsp` | Make room for locals / alignment |
| Release stack space | `addq $32, %rsp` | Undo a previous subtraction |

## Patterns to Memorize

- Function return in `eax` or `rax`
- Integer arguments in `edi`, `esi`, `edx`, `ecx`, `r8d`, `r9d`
- `cmp` writes flags, not registers
- `lea` computes addresses; `mov` loads or stores memory
