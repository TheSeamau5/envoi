// expect_stdout: 6
#include <stdio.h>

int gcd(int a, int b) {
    while (b != 0) {
        int t = a;
        a = b;
        b = t - (t / b) * b;
    }
    return a;
}

int main() {
    printf("%d\n", gcd(54, 24));
    return 0;
}
