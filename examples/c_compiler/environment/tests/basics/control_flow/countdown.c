// expect_stdout: 3
// expect_stdout: 2
// expect_stdout: 1
#include <stdio.h>

int main() {
    int i = 3;
    while (i > 0) {
        printf("%d\n", i);
        i = i - 1;
    }
    return 0;
}
