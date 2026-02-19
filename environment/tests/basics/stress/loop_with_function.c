// expect_stdout: 385
#include <stdio.h>

int square(int x) {
    return x * x;
}

int main() {
    int sum = 0;
    int i = 1;
    while (i <= 10) {
        sum = sum + square(i);
        i = i + 1;
    }
    printf("%d\n", sum);
    return 0;
}
