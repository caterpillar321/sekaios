Microsoft 타사용 UEFI CA 인증서 (공개 인증서 — Microsoft 가 배포하는 그대로)

  MSCA2023.CER  Microsoft UEFI CA 2023              https://go.microsoft.com/fwlink/?linkid=2239872
  MSCA2011.CER  Microsoft Corporation UEFI CA 2011  https://go.microsoft.com/fwlink/p/?linkid=321194

SekaiOS 의 부트로더(shim)는 이 두 인증서로 서명돼 있습니다.
"Secure Boot Violation / Invalid signature detected" 가 뜨는 PC(Secured-core PC — 일부 새 노트북)는
펌웨어에 이 인증서가 빠져 있습니다. 보안 부팅을 끄지 말고 BIOS 에서 넣어 주세요:

  1. Windows 에서 BitLocker(장치 암호화)를 먼저 일시 중단  (관리자 터미널)
       manage-bde -protectors -disable C: -RebootCount 3
  2. BIOS › Security › Secure Boot › Key Management › Authorized Signatures (db) › Append
     → 이 USB 의 EFI/certs/MSCA2023.CER (Public Key Certificate) — MSCA2011.CER 도 같은 방법으로
  3. 저장하고 다시 USB 로 부팅

되돌리기: Key Management › Restore Factory Keys
