/* MafuyuMom 시험용 설치 프로그램 — 창 없이 mmhello.exe 를 C:\Program Files\MM Hello 에 복사하고
   시작 메뉴(모든 사용자)에 "MM Hello" · "Uninstall MM Hello" 바로 가기(.lnk)를 IShellLink 로 만든다.
   mmhello.exe 는 이 설치 파일 옆에 있어야 한다. */
#define COBJMACROS
#include <windows.h>
#include <shlobj.h>
#include <shobjidl.h>
#include <objidl.h>

static int make_link(const WCHAR *target, const WCHAR *lnk, const WCHAR *desc)
{
    IShellLinkW *sl;
    IPersistFile *pf;
    if (FAILED(CoCreateInstance(&CLSID_ShellLink, NULL, CLSCTX_INPROC_SERVER, &IID_IShellLinkW, (void **)&sl))) return 0;
    IShellLinkW_SetPath(sl, target);
    IShellLinkW_SetDescription(sl, desc);
    IShellLinkW_SetIconLocation(sl, target, 0);
    int ok = 0;
    if (SUCCEEDED(IShellLinkW_QueryInterface(sl, &IID_IPersistFile, (void **)&pf))) {
        ok = SUCCEEDED(IPersistFile_Save(pf, lnk, TRUE));
        IPersistFile_Release(pf);
    }
    IShellLinkW_Release(sl);
    return ok;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, PWSTR cmd, int show)
{
    WCHAR me[MAX_PATH], src[MAX_PATH], pf[MAX_PATH], dir[MAX_PATH], dst[MAX_PATH], menu[MAX_PATH], lnk[MAX_PATH];
    GetModuleFileNameW(NULL, me, MAX_PATH);
    lstrcpyW(src, me);
    WCHAR *slash = src + lstrlenW(src);
    while (slash > src && *slash != L'\\') slash--;
    lstrcpyW(slash + 1, L"mmhello.exe");

    SHGetFolderPathW(NULL, CSIDL_PROGRAM_FILES, NULL, 0, pf);
    wsprintfW(dir, L"%s\\MM Hello", pf);
    CreateDirectoryW(dir, NULL);
    wsprintfW(dst, L"%s\\mmhello.exe", dir);
    if (!CopyFileW(src, dst, FALSE)) return 2;
    Sleep(1500);                                  /* 진짜 설치 프로그램처럼 잠깐 */

    CoInitialize(NULL);
    SHGetFolderPathW(NULL, CSIDL_COMMON_PROGRAMS, NULL, 0, menu);
    wsprintfW(dir, L"%s\\MM Hello", menu);
    CreateDirectoryW(dir, NULL);
    wsprintfW(lnk, L"%s\\MM Hello.lnk", dir);
    int a = make_link(dst, lnk, L"MafuyuMom 시험 앱");
    wsprintfW(lnk, L"%s\\Uninstall MM Hello.lnk", dir);
    int b = make_link(dst, lnk, L"제거");
    CoUninitialize();
    return (a && b) ? 0 : 3;
}
