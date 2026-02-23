// expect_stdout: medium
#include <stdio.h>

int main() {
    int x = 50;
    if (x > 100) {
        printf("big\n");
    } else {
        if (x > 10) {
            printf("medium\n");
        } else {
            printf("small\n");
        }
    }
    return 0;
}
