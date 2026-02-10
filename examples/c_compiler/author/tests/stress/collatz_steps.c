// expect_stdout: 111
#include <stdio.h>

int collatz(int n) {
    int steps = 0;
    while (n != 1) {
        if (n - (n / 2) * 2 == 0) {
            n = n / 2;
        } else {
            n = 3 * n + 1;
        }
        steps = steps + 1;
    }
    return steps;
}

int main() {
    printf("%d\n", collatz(27));
    return 0;
}
