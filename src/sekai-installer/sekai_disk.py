"""디스크 이름 — 설치 화면(sekai-installer)과 파티션 도우미(sekai-partition, root)가 같이 쓴다 (GTK 없음).
제조사 자리의 ATA·장치 번호(0x1af4)는 빼고, 모델 이름이 없으면 연결 방식으로 (컴퓨터 관리 › 디스크 관리와 같은 이름)."""
import re


def disk_name(d):
    vendor = (d.get("vendor") or "").strip().rstrip(",").strip()
    model = (d.get("model") or "").strip().replace("_", " ")
    if vendor and model.lower().startswith(vendor.lower()):
        vendor = ""                                  # "VMware, VMware Virtual S" → 한 번만
    if vendor.upper() == "ATA":                      # SATA 디스크는 제조사 자리에 ATA 가 온다
        vendor = ""
    if re.fullmatch(r"0x[0-9a-fA-F]+", vendor):       # 가상 디스크(virtio)는 제조사 자리에 장치 번호(0x1af4)가 온다
        vendor = ""
    name = " ".join(x for x in (vendor, model) if x)
    if name:
        return name
    # 모델 이름이 없다 — 연결 방식으로 (컴퓨터 관리 › 디스크 관리와 같은 이름)
    tran = (d.get("tran") or "").lower()
    if tran == "virtio" or d.get("name", "").startswith("vd"):
        return "가상 디스크"
    if tran == "nvme":
        return "NVMe SSD"
    if tran == "usb":
        return "USB 드라이브" if d.get("rm") in (True, 1, "1") else "외장 디스크"
    return "SSD" if d.get("rota") in (False, 0, "0") else "하드 디스크"
