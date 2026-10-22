"""계정 관리 (서버에서 실행)

  docker compose exec auth python manage.py add <아이디> [표시이름]   계정 만들기 (비밀번호는 물어봄)
  docker compose exec auth python manage.py passwd <아이디>           비밀번호 바꾸기 (모든 기기 로그아웃)
  docker compose exec auth python manage.py del <아이디>              계정 지우기 (세션·업로드 토큰도 함께)
  docker compose exec auth python manage.py list                      계정 목록
수집 데이터·브리핑 폴더(data/users/<아이디>, output/users/<아이디>)는 지우지 않음
"""
import getpass
import sqlite3
import sys
import time

import store


def ask_pw():
    pw = getpass.getpass("비밀번호 (8자 이상): ")
    if len(pw) < 8:
        sys.exit("8자 이상이어야 합니다")
    if getpass.getpass("한 번 더: ") != pw:
        sys.exit("두 번 입력한 비밀번호가 다릅니다")
    return pw


def main(argv):
    store.init()
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "add" and args:
        try:
            store.add_user(args[0], ask_pw(), " ".join(args[1:]))
        except ValueError as e:
            sys.exit(str(e))
        except sqlite3.IntegrityError:
            sys.exit(f"이미 있는 아이디: {args[0]}")
        print(f"계정을 만들었습니다: {args[0]}")
    elif cmd == "passwd" and args:
        print("바꿨습니다" if store.set_pw(args[0], ask_pw()) else f"없는 아이디: {args[0]}")
    elif cmd == "del" and args:
        print("지웠습니다" if store.del_user(args[0]) else f"없는 아이디: {args[0]}")
    elif cmd == "list":
        for uid, name, created in store.users():
            print(f"{uid}\t{name}\t{time.strftime('%Y-%m-%d', time.localtime(created))}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
