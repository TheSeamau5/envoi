// expect_stdout: 18
#include <stdio.h>

int main() {
    printf("%d\n", (1 + 2) * (3 + (1 + 2)));
    return 0;
}
