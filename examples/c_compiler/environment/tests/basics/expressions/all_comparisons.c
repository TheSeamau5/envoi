// expect_stdout: 1
// expect_stdout: 1
// expect_stdout: 1
// expect_stdout: 1
// expect_stdout: 1
// expect_stdout: 1
#include <stdio.h>

int check(int v) {
    if (v) {
        printf("%d\n", 1);
    } else {
        printf("%d\n", 0);
    }
    return 0;
}

int main() {
    check(3 < 5);
    check(5 > 3);
    check(3 <= 3);
    check(3 >= 3);
    check(7 == 7);
    check(7 != 8);
    return 0;
}
