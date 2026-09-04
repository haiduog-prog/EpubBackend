"""
Script hỗ trợ đăng nhập Google Drive qua OAuth 2.0 dành cho tài khoản Google cá nhân (@gmail.com).
Tạo file token credentials tự động làm mới (refresh token), cho phép đồng bộ sử dụng
15 GB dung lượng miễn phí của tài khoản cá nhân, tránh lỗi hạn ngạch 0 MB của Service Account.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]


def main():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("[ERROR] Thiếu thư viện google-auth-oauthlib.")
        print("Vui lòng cài đặt bằng lệnh: pip install google-auth-oauthlib")
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Google Drive OAuth 2.0 Login")
    parser.add_argument(
        "--client-secrets",
        "-c",
        default="oauth-credentials.json",
        help="Đường dẫn tới file OAuth Client ID JSON tải từ Google Cloud Console (mặc định: oauth-credentials.json)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="google-credentials.json",
        help="Đường dẫn file lưu credentials/token (mặc định: google-credentials.json)",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=0,
        help="Cổng local server nhận callback (mặc định: 0 - tự động chọn cổng trống)",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    secrets_path = Path(args.client_secrets)
    if not secrets_path.is_absolute():
        secrets_path = project_root / secrets_path

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = project_root / output_path

    if not secrets_path.exists():
        candidates = [
            f for f in project_root.glob("*.json")
            if "client_secret" in f.name.lower() or "oauth" in f.name.lower()
        ]
        if candidates:
            secrets_path = candidates[0]
            print(f"[*] Tự động tìm thấy file client secrets: {secrets_path.name}")
        else:
            print("=" * 72)
            print("         HƯỚNG DẪN TẠO OAUTH 2.0 CLIENT ID TRÊN GOOGLE CLOUD")
            print("=" * 72)
            print("1. Mở Google Cloud Console:")
            print("   https://console.cloud.google.com/apis/credentials")
            print("2. Đảm bảo đã chọn đúng Project (ví dụ: my-moneynote-ecf90).")
            print("3. Kiểm tra 'Màn hình đồng ý OAuth' (OAuth consent screen):")
            print("   - User Type: External (Bên ngoài).")
            print("   - Thêm email @gmail.com của bạn vào danh sách 'Test users' (Người dùng thử nghiệm).")
            print("4. Vào mục 'Thông tin xác thực' (Credentials):")
            print("   - Bấm '+ TẠO THÔNG TIN XÁC THỰC' (+ CREATE CREDENTIALS)")
            print("   - Chọn 'ID ứng dụng OAuth' (OAuth client ID)")
            print("   - Loại ứng dụng: Chọn 'Ứng dụng máy tính' (Desktop app)")
            print("   - Tên: EpubBackend Desktop")
            print("   - Bấm 'Tạo' (Create)")
            print("5. Bấm nút Tải xuống JSON (Download JSON).")
            print(f"6. Đổi tên file tải về thành 'oauth-credentials.json' và đặt vào thư mục:")
            print(f"   {project_root}")
            print("7. Chạy lại script:")
            print("   python scripts/google_drive_login.py")
            print("=" * 72)
            sys.exit(1)

    print(f"[*] Đang tải client secrets từ: {secrets_path.name}")
    print("[*] Đang khởi động trình duyệt để bạn đăng nhập tài khoản Google...")
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), scopes=SCOPES)
        creds = flow.run_local_server(port=args.port, prompt="consent")
    except Exception as exc:
        print(f"[ERROR] Lỗi trong quá trình xác thực: {exc}")
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(creds.to_json(), encoding="utf-8")

    print("\n" + "=" * 72)
    print(f"[SUCCESS] ĐĂNG NHẬP THÀNH CÔNG!")
    print(f"Token đã được lưu tại: {output_path.resolve()}")
    print("Thông tin xác thực này sẽ tự động refresh vĩnh viễn khi hết hạn.")
    print("Dung lượng upload sẽ được tính vào 15 GB của tài khoản Google cá nhân.")
    print("=" * 72)


if __name__ == "__main__":
    main()
