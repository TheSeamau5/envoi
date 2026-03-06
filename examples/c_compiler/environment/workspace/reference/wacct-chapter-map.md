# WACCT Chapter Map

The test suite under `/opt/tests/wacct/tests/` is organized by chapter. Complete each chapter's tests before moving to the next one. Earlier chapters are prerequisites: if chapter 5 is broken, later chapters will fail for secondary reasons.

The compiler target in this environment is C23. The Nora Sandler book tests cover the book's feature set (roughly C11/C17-era compiler construction), but the broader envoi suites may additionally exercise C23 behavior.

## Part I: The Basics

| Chapter | Feature | Why it matters |
| --- | --- | --- |
| 1 | A Minimal Compiler | Return statements only: `int main(void) { return N; }` |
| 2 | Unary Operators | `~`, unary `-`, logical `!` |
| 3 | Binary Operators | `+`, `-`, `*`, `/`, `%`, precedence, associativity |
| 4 | Logical and Relational Operators | `&&`, `||`, `==`, `!=`, `<`, `>`, `<=`, `>=` |
| 5 | Local Variables | Declarations, assignment, compound assignment |
| 6 | If Statements and Conditional Expressions | `if`, `else`, ternary operator |
| 7 | Compound Statements | Blocks and variable scope |
| 8 | Loops | `while`, `for`, `do-while`, `break`, `continue` |
| 9 | Functions | Declarations, definitions, calls, parameters, forward declarations |
| 10 | File-scope Variables and Storage-class Specifiers | Globals, `static`, `extern` |

Natural extension point after Chapter 9: multi-file compilation and linking. The wacct fixtures are single-file, but the basics suite in this environment also includes multi-file cases once functions are solid.

## Part II: Types Beyond `int`

| Chapter | Feature | Why it matters |
| --- | --- | --- |
| 11 | Long Integers | 64-bit integers and conversions |
| 12 | Unsigned Integers | Unsigned arithmetic and mixed signedness |
| 13 | Floating-Point Numbers | `double`, `float`, conversions |
| 14 | Pointers | Address-of, dereference, pointer types |
| 15 | Arrays and Pointer Arithmetic | Indexing, decay, scaled addressing |
| 16 | Characters and Strings | `char`, string literals, escapes |
| 17 | Supporting Dynamic Memory Allocation | `malloc`, `free`, `void *` |
| 18 | Structures | Layout, field access, nested structs |

## Part III: Optimizations

| Chapter | Feature | Why it matters |
| --- | --- | --- |
| 19 | Optimizing Tacky Programs | Constant folding and dead-code elimination |
| 20 | Register Allocation | Graph-coloring register allocation |

## Practical Build Order

1. Finish the current chapter before sampling a later one.
2. Re-run basics after every chapter boundary.
3. When a later chapter fails, first confirm the earlier prerequisite chapters are still green.
