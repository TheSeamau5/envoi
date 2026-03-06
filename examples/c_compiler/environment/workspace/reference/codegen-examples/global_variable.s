	.text
	.globl	counter
	.bss
	.align 4
	.type	counter, @object
	.size	counter, 4
counter:
	.zero	4
	.text
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	$5, counter(%rip)
	movl	counter(%rip), %eax
	addl	$3, %eax
	movl	%eax, counter(%rip)
	movl	counter(%rip), %eax
	popq	%rbp
	ret
	.size	main, .-main
