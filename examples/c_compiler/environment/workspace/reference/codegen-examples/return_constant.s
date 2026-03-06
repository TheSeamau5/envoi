	.text
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	$42, %eax
	popq	%rbp
	ret
	.size	main, .-main
