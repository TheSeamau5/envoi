// expect_stdout: 1
// expect_stdout: 0
#include <stdio.h>

int main() {
    int x = 5;
    if (x > 2) {
        printf("%d\n", 1);
    } else {
        printf("%d\n", 0);
    }
    if (x > 10) {
        printf("%d\n", 1);
    } else {
        printf("%d\n", 0);
    }
    return 0;
}
