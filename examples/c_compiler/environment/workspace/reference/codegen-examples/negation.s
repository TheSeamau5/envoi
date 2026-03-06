	.text
	.globl	negate
	.type	negate, @function
negate:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%edi, -4(%rbp)
	movl	-4(%rbp), %eax
	negl	%eax
	popq	%rbp
	ret
	.size	negate, .-negate
	.globl	bitwise_not
	.type	bitwise_not, @function
bitwise_not:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%edi, -4(%rbp)
	movl	-4(%rbp), %eax
	notl	%eax
	popq	%rbp
	ret
	.size	bitwise_not, .-bitwise_not
	.globl	logical_not
	.type	logical_not, @function
logical_not:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%edi, -4(%rbp)
	cmpl	$0, -4(%rbp)
	sete	%al
	movzbl	%al, %eax
	popq	%rbp
	ret
	.size	logical_not, .-logical_not
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	pushq	%rbx
	movl	$5, %edi
	call	negate
	movl	%eax, %ebx
	movl	$1, %edi
	call	bitwise_not
	addl	%eax, %ebx
	movl	$0, %edi
	call	logical_not
	addl	%ebx, %eax
	movq	-8(%rbp), %rbx
	leave
	ret
	.size	main, .-main
