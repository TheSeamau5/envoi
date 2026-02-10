// expect_stdout: 30
#include <stdio.h>

int square(int x) {
    return x * x;
}

int add(int a, int b) {
    return a + b;
}

int sum_of_squares(int a, int b) {
    return add(square(a), square(b));
}

int main() {
    printf("%d\n", sum_of_squares(3, add(1, 3)));
    return 0;
}
