#!/bin/bash
# 켄 — 노트북(윈도우 11)과 같은 배치의 가짜 윈도우 디스크: ESP 260M(bootmgfw.efi) · MSR 16M · C: NTFS 20G · (빈 공간) · WinRE 2G · 복구 260M
# 사용: sudo mkwin.sh 이미지 [크기]  → 루프 장치 이름을 찍고, 루프는 붙인 채로 둔다 (부른 쪽이 losetup -d)
set -e
IMG=$1; SIZE=${2:-40G}
rm -f "$IMG"; truncate -s "$SIZE" "$IMG"
sgdisk -o "$IMG" >/dev/null
sgdisk -n 1:0:+260M -t 1:ef00 -c 1:"EFI system partition" "$IMG" >/dev/null
sgdisk -n 2:0:+16M -t 2:0c01 -c 2:"Microsoft reserved partition" "$IMG" >/dev/null
sgdisk -n 3:0:+20G -t 3:0700 -c 3:"Basic data partition" "$IMG" >/dev/null
END=$(sgdisk -E "$IMG")
# 맨 뒤에 복구 둘 (윈도우 11 배치) — 끝에서부터 260M, 그 앞 2G
S5=$(( END - 260*2048 + 1 )); S4=$(( S5 - 2*1024*2048 ))
sgdisk -n 4:$S4:$(( S5 - 1 )) -t 4:2700 -c 4:"Basic data partition" "$IMG" >/dev/null
sgdisk -n 5:$S5:$END -t 5:2700 -c 5:"Basic data partition" "$IMG" >/dev/null
LO=$(losetup -fP --show "$IMG")
mkfs.vfat -F 32 -n SYSTEM ${LO}p1 >/dev/null
mkntfs -Q -L OS ${LO}p3 >/dev/null 2>&1
mkntfs -Q -L WinRE ${LO}p4 >/dev/null 2>&1
mkntfs -Q -L Recovery ${LO}p5 >/dev/null 2>&1
M=$(mktemp -d); mount ${LO}p1 $M; mkdir -p $M/EFI/Microsoft/Boot $M/EFI/Boot
echo fake > $M/EFI/Microsoft/Boot/bootmgfw.efi; echo fake > $M/EFI/Microsoft/Boot/BCD; echo fake > $M/EFI/Boot/bootx64.efi   # os-prober 는 BCD 와 bootmgfw.efi 가 둘 다 있어야 Windows 로 본다
umount $M; rmdir $M
echo $LO
