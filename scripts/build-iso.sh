#!/bin/bash
# SekaiOS — ISO 빌드
# 사용법: ~/MyOS/scripts/build-iso.sh [--win]
#   --win : 완성된 ISO 를 Windows Downloads 로 복사
set -euo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
P="$(dirname "$SELF")"

ISO_NAME="sekaios-1.0-amd64.iso"
VOLID="SEKAIOS"
SQUASH="$P/build/filesystem.zstd.squashfs"
REFIND="$P/build/refind/usr/share/refind"
# ISO 를 복사할 Windows 폴더 (--win) — 이 컴퓨터 전용 설정 local/env 의 SEKAI_WINDIR
[ -f "$P/local/env" ] && . "$P/local/env"
WINDIR="${SEKAI_WINDIR:-}"

say(){ printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# ── 1. live/ 동기화 ──────────────────────────────────
say "커널 / initramfs / squashfs 동기화"
cp -u "$P"/rootfs/boot/vmlinuz-*    "$P/iso/live/vmlinuz"
cp -u "$P"/rootfs/boot/initrd.img-* "$P/iso/live/initrd.img"
[ -f "$SQUASH" ] || { echo "E: squashfs 없음: $SQUASH"; exit 1; }
cp -u "$SQUASH" "$P/iso/live/filesystem.squashfs"
printf '%s' "$(stat -c%s "$P/iso/live/filesystem.squashfs")" > "$P/iso/live/filesystem.size"

# ── 1-b. BIOS 부팅(isolinux) — 설정은 config/, 바이너리는 syslinux 패키지에서 ──
mkdir -p "$P/iso/isolinux"
cp "$P/config/isolinux.cfg" "$P/iso/isolinux/isolinux.cfg"
cp -u /usr/lib/ISOLINUX/isolinux.bin "$P/iso/isolinux/"
for m in ldlinux libcom32 libutil menu vesamenu; do
    cp -u "/usr/lib/syslinux/modules/bios/$m.c32" "$P/iso/isolinux/"
done

# ── 2. efiboot.img 재생성 ────────────────────────────
say "efiboot.img 생성 (FAT, 72MB — 커널/initrd 포함)"
IMG="$P/build/efiboot.img"
rm -f "$IMG"
mkfs.vfat -C -n SEKAIEFI "$IMG" 73728 > /dev/null
mmd -i "$IMG" ::/EFI ::/EFI/BOOT ::/EFI/BOOT/drivers_x64 \
              ::/EFI/BOOT/icons ::/EFI/BOOT/fonts ::/EFI/BOOT/banners ::/live
mcopy -i "$IMG"    "$REFIND/refind/refind_x64.efi"              ::/EFI/BOOT/BOOTX64.EFI
mcopy -i "$IMG"    "$REFIND/refind/drivers_x64/iso9660_x64.efi" ::/EFI/BOOT/drivers_x64/
mcopy -i "$IMG" -s "$REFIND"/refind/icons/*.png                 ::/EFI/BOOT/icons/
mcopy -i "$IMG"    "$P/src/sekai-desktop/usr/share/sekai/refind/os_sekai.png" ::/EFI/BOOT/icons/os_sekai.png
mmd   -i "$IMG" ::/EFI/BOOT/themes ::/EFI/BOOT/themes/sekai
for f in background selection_big selection_small; do
    mcopy -i "$IMG" "$P/src/sekai-desktop/usr/share/sekai/refind/$f.png" ::/EFI/BOOT/themes/sekai/$f.png
done
mcopy -i "$IMG" -s "$REFIND"/fonts/*.png                        ::/EFI/BOOT/fonts/
mcopy -i "$IMG"    "$REFIND"/banners/refind_banner.png          ::/EFI/BOOT/banners/
mcopy -i "$IMG"    "$P/config/refind-live.conf"                       ::/EFI/BOOT/refind.conf
# ★ 커널과 initramfs 를 FAT 안에 — 펌웨어가 드라이버 없이 읽을 수 있게
mcopy -i "$IMG"    "$P/iso/live/vmlinuz"                        ::/live/vmlinuz
mcopy -i "$IMG"    "$P/iso/live/initrd.img"                     ::/live/initrd.img

# ── 3. ISO 트리의 EFI 쪽도 갱신 ──────────────────────
say "ISO 트리 EFI 갱신"
mkdir -p "$P"/iso/EFI/BOOT/{drivers_x64,icons,fonts,banners} "$P/iso/boot/grub"
cp "$REFIND/refind/refind_x64.efi"              "$P/iso/EFI/BOOT/BOOTX64.EFI"
cp "$REFIND/refind/drivers_x64/iso9660_x64.efi" "$P/iso/EFI/BOOT/drivers_x64/"
cp -u "$REFIND"/refind/icons/*.png              "$P/iso/EFI/BOOT/icons/"
cp "$P/src/sekai-desktop/usr/share/sekai/refind/os_sekai.png" "$P/iso/EFI/BOOT/icons/os_sekai.png"
mkdir -p "$P/iso/EFI/BOOT/themes/sekai"
cp "$P"/src/sekai-desktop/usr/share/sekai/refind/{background,selection_big,selection_small}.png "$P/iso/EFI/BOOT/themes/sekai/"
cp -u "$REFIND"/fonts/*.png                     "$P/iso/EFI/BOOT/fonts/"
cp -u "$REFIND"/banners/refind_banner.png       "$P/iso/EFI/BOOT/banners/"
cp "$P/config/refind-live.conf"                       "$P/iso/EFI/BOOT/refind.conf"
cp "$IMG"                                       "$P/iso/boot/grub/efiboot.img"

# ── 4. ISO 굽기 ──────────────────────────────────────
say "xorriso 실행"
rm -f "$P/build/$ISO_NAME"
xorriso -as mkisofs \
  -iso-level 3 -volid "$VOLID" \
  -full-iso9660-filenames -joliet -joliet-long -rational-rock \
  -isohybrid-mbr /usr/lib/ISOLINUX/isohdpfx.bin -partition_offset 16 \
  -b isolinux/isolinux.bin -c isolinux/boot.cat \
  -no-emul-boot -boot-load-size 4 -boot-info-table \
  -eltorito-alt-boot -e boot/grub/efiboot.img -no-emul-boot -isohybrid-gpt-basdat \
  -o "$P/build/$ISO_NAME" "$P/iso" 2>&1 | grep -E 'ISO image produced|completed|FAILURE|WARNING' || true

# ── 5. 결과 ──────────────────────────────────────────
say "완료"
ls -lh "$P/build/$ISO_NAME" | awk '{print "    "$5"  "$9}'
md5sum "$P/build/$ISO_NAME" | awk '{print "    md5 "$1}'

if [ "${1:-}" = "--win" ] && [ -z "$WINDIR" ]; then
  say "Windows 복사 생략 — local/env 에 SEKAI_WINDIR 가 없습니다"
elif [ "${1:-}" = "--win" ]; then
  # 주의: 같은 파일 이름으로 덮어쓰면 안 된다.
  #   VMware 가 그 ISO 를 /dev/sr0 로 물고 있는 상태에서 내용을 바꾸면
  #   돌고 있는 라이브 세션의 squashfs 가 그 자리에서 깨진다
  #   (SQUASHFS error: zstd decompression failed → 실행 파일이 I/O error).
  #   그래서 빌드마다 새 이름으로 쓰고, 오래된 것은 3개만 남긴다.
  STAMP="$(date +%Y%m%d-%H%M)"
  WIN_NAME="${ISO_NAME%.iso}-${STAMP}.iso"
  say "Windows 로 복사: $WINDIR/$WIN_NAME"
  cp "$P/build/$ISO_NAME" "$WINDIR/$WIN_NAME"
  ls -lh "$WINDIR/$WIN_NAME" | awk '{print "    "$5"  "$9}'
  # 오래된 빌드 정리 (최근 3개 유지)
  ls -1t "$WINDIR/${ISO_NAME%.iso}"-*.iso 2>/dev/null | tail -n +4 | while read -r old_iso; do
    echo "    오래된 빌드 삭제: $(basename "$old_iso")"
    rm -f "$old_iso" 2>/dev/null || echo "      (사용 중이라 못 지움 — VMware 에 연결돼 있을 수 있음)"
  done
  echo "    ※ VMware 에서 이 파일을 CD/DVD 로 새로 지정해 주세요."
fi
