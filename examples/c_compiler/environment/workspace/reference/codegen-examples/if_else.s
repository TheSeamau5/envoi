	.text
	.section	.rodata
.LC0:
	.string	"big"
.LC1:
	.string	"small"
	.text
	.globl	main
	.type	main, @function
main:
	pushq	%rbp
	movq	%rsp, %rbp
	subq	$16, %rsp
	movl	$7, -4(%rbp)
	cmpl	$5, -4(%rbp)
	jle	.L2
	movl	$.LC0, %edi
	call	puts
	jmp	.L3
.L2:
	movl	$.LC1, %edi
	call	puts
.L3:
	movl	$0, %eax
	leave
	ret
	.size	main, .-main
