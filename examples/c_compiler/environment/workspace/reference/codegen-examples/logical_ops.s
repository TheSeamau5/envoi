	.text
	.globl	decide
	.type	decide, @function
decide:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%edi, -4(%rbp)
	movl	%esi, -8(%rbp)
	movl	%edx, -12(%rbp)
	cmpl	$0, -4(%rbp)
	je	.L2
	cmpl	$0, -8(%rbp)
	jne	.L3
.L2:
	cmpl	$0, -12(%rbp)
	je	.L4
.L3:
	movl	$1, %eax
	jmp	.L6
.L4:
	movl	$0, %eax
.L6:
	popq	%rbp
	ret
	.size	decide, .-decide
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	$7, %edx
	movl	$0, %esi
	movl	$1, %edi
	call	decide
	popq	%rbp
	ret
	.size	main, .-main
