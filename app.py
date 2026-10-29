"""Запуск Terminal Simulator с выбором доступного GUI."""


def main():
    try:
        from app_qt import main as qt_main
    except ModuleNotFoundError as error:
        if error.name != "qt_bindings":
            raise
        try:
            from tk_app import main as tk_main
        except ModuleNotFoundError as error:
            if error.name != "tkinter":
                raise
            raise RuntimeError(
                "Нет GUI-библиотеки. Установите PySide6 (`python -m pip install PySide6`) "
                "или PyQt6 (`python -m pip install PyQt6`); Tkinter обычно входит в Python."
            ) from error

        tk_main()
    else:
        qt_main()


if __name__ == "__main__":
    main()
