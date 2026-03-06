	.text
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	$1, -4(%rbp)
	movl	$0, -8(%rbp)
	jmp	.L2
.L3:
	movl	-4(%rbp), %eax
	addl	%eax, -8(%rbp)
	addl	$1, -4(%rbp)
.L2:
	cmpl	$5, -4(%rbp)
	jle	.L3
	movl	-8(%rbp), %eax
	popq	%rbp
	ret
	.size	main, .-main
