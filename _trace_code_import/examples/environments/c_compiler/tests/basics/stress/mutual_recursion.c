// expect_stdout: 1
// expect_stdout: 0
#include <stdio.h>

int is_odd(int n);

int is_even(int n) {
    if (n == 0) {
        return 1;
    }
    return is_odd(n - 1);
}

int is_odd(int n) {
    if (n == 0) {
        return 0;
    }
    return is_even(n - 1);
}

int main() {
    printf("%d\n", is_even(10));
    printf("%d\n", is_even(7));
    return 0;
}
