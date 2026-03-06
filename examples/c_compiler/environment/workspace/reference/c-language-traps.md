# C23 Language Traps

These are the recurring C semantics bugs that break compiler implementations.

## Integer Promotions

- Small integer types promote before most arithmetic.
- `char` signedness is implementation-defined.
- On x86_64 gcc, plain `char` is signed by default.
- Example: `char a = 200; int b = a;` often becomes `-56`, not `200`.

## Signed Overflow

- Signed integer overflow is undefined behavior in the language.
- The oracle compares behavior against gcc in this environment, so match gcc's emitted behavior on tested programs.
- Do not invent your own overflow traps unless a test explicitly expects them.

## Operator Precedence

- `*p++` means `*(p++)`, not `(*p)++`.
- Prefix/postfix binding mistakes are common parser bugs.

## Short-Circuit Evaluation

- `&&` and `||` must skip the right operand when the left operand already decides the result.
- `f() && g()` must not call `g()` when `f()` returns `0`.
- `f() || g()` must not call `g()` when `f()` returns nonzero.

## Sequence Points and Undefined Behavior

- Expressions like `i = i++` are undefined.
- The tests generally avoid pure UB, but if a case is compared against gcc, your observable behavior still needs to match gcc.

## Array Decay

- Arrays decay to pointers in most expressions.
- Important exceptions include `sizeof array`, `_Alignof`, and unary `&`.

## Struct Padding and Alignment

- Members align to their natural alignment.
- `struct { char a; int b; }` usually has 3 bytes of padding after `a` and total size 8 on x86_64.

## C23 Keywords: `bool`, `true`, `false`

- In C23 these are keywords, not macros from `<stdbool.h>`.
- Treat them as reserved words in the lexer and parser.

## C23 `nullptr`

- C23 adds `nullptr` and type `nullptr_t`.
- It is a null pointer constant with its own type.
- It converts implicitly to pointer types.

## C23 Empty Initializers

- `int x = {};` is valid C23 and zero-initializes the object.
- This applies to scalars, arrays, and structs.

## C23 Digit Separators

- C23 allows `'` inside numeric literals: `1'000'000`, `0xFF'FF`, `1'000.0`.
- The lexer must ignore the separators while preserving the numeric value.

## C23 `constexpr`

- C23 adds `constexpr` as a storage-class specifier for objects.
- This is not C++ `constexpr` for functions.

## C23 `typeof` and `typeof_unqual`

- C23 standardizes `typeof(expr)` and `typeof_unqual(expr)`.
- They produce types, not runtime values.
