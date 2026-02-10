// expect_stdout: deep
#include <stdio.h>

int main() {
    int x = 10;
    if (x > 5) {
        if (x > 8) {
            if (x > 9) {
                printf("deep\n");
            }
        }
    }
    return 0;
}
