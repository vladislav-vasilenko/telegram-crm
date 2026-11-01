import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Локальная Telegram CRM")
    parser.add_argument("command", nargs="?", choices=["serve", "scan", "report", "folders", "triage"], default="serve")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("TG_CRM_DATA_DIR", Path(__file__).resolve().parent.parent / ".data")))
    parser.add_argument("--port", type=int, default=4180)
    parser.add_argument("--demo", action="store_true", help="Только синтетические демонстрационные данные")
    parser.add_argument("--romantic", type=Path, help="triage: JSON с ID чатов, проверенных вручную: {\"likely\": [], \"not\": []}")
    parser.add_argument("--apply", action="store_true", help="Создать/обновить только папки CRM в Telegram")
    args = parser.parse_args()
    process_lock = None
    if args.command in ("serve", "scan", "triage") or (args.command == "folders" and args.apply):
        args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        process_lock = (args.data_dir / "process.lock").open("a")
        try:
            fcntl.flock(process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "Сервис уже использует эту базу. Откройте панель или сначала остановите сервер.\n")
    if args.command == "serve":
        import uvicorn
        from .server import create_app
        print(f"Панель: http://127.0.0.1:{args.port}")
        uvicorn.run(create_app(args.data_dir, demo=args.demo), host="127.0.0.1", port=args.port, log_level="warning")
        return
    from .analysis import classify
    from .store import Store
    from .telegram import TelegramService
    store = Store(args.data_dir)

    if args.command == "triage":
        from collections import Counter
        from .triage import build_triage
        romantic = json.loads(args.romantic.read_text()) if args.romantic else None
        result = build_triage(store, romantic)
        print(json.dumps(Counter(v["bucket"] for v in result["chats"].values()), ensure_ascii=False, indent=2))
        store.close()
        return

    async def run():
        service = TelegramService(store)
        try:
            if args.command == "report":
                print(json.dumps([classify(**r, settings=store.settings()) for r in store.all()], ensure_ascii=False, indent=2))
            elif args.command == "folders" and not args.apply:
                print(json.dumps(service.folder_plan(), ensure_ascii=False, indent=2))
            else:
                await service.restore()
                service.require_ready()
                if args.command == "scan":
                    await service.scan()
                    print(json.dumps(service.status, ensure_ascii=False))
                    if service.status.get("error"):
                        raise SystemExit(1)
                elif args.apply:
                    print(json.dumps(await service.apply_folders(), ensure_ascii=False))
        finally:
            await service.close()
            store.close()
    try:
        asyncio.run(run())
    except (ValueError, OSError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
