#!/usr/bin/env python3
"""Скрипт для интерактивного сбора точек через Simple Joystick."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from API import RobotApi
from API.types import DigitalIndex, PositionOrientation

# Импортируем конфиги из соседнего файла
from config import RobotConfig, gripper  # type: ignore


def format_pose_for_file(pose: PositionOrientation, timestamp: datetime) -> str:
    """Форматирует позицию робота в строку для записи в файл."""
    # pose = (X, Y, Z, Rx, Ry, Rz) — метры и градусы
    return (
        f"{timestamp.strftime('%Y-%m-%d %H:%M:%S')} | "
        f"[{pose[0]:.6f}, {pose[1]:.6f}, {pose[2]:.6f}, "
        f"{pose[3]:.2f}, {pose[4]:.2f}, {pose[5]:.2f}\n"
    )


def main() -> None:
    """Основной цикл сбора точек."""
    # Файл для сохранения — в той же папке, где скрипт
    output_file = Path(__file__).parent / "collected_points.txt"

    print(f"🔌 Подключение к роботу {RobotConfig.IP}...")
    
    # Подключаемся в полноценном режиме (нужен для simple_joystick)
    robot = RobotApi(
        ip=RobotConfig.IP,
        show_std_traceback=True,
        autoconnect=True,
        read_only=False,
    )

    if not robot.is_connected():
        print("❌ Не удалось подключиться к роботу")
        return

    print("✅ Робот подключен. Джойстик готов к запуску.\n")
    print("💡 Инструкция:")
    print("   • Нажми <Enter> → запустится джойстик для позиционирования")
    print("   • Введи любой символ + <Enter> → выход из программы\n")

    try:
        while True:
            # 1. Спрашиваем пользователя
            user_input = input("➤ Получить новую точку? (Enter = да, любой символ = выход): ").strip()
            
            # 2. Если ввод НЕ пустой — завершаем
            if user_input:
                print("👋 Завершение работы...")
                break

            # 3. Запускаем простой джойстик (блокирующий вызов)
            print("🎮 Запуск Simple Joystick... (закрой окно, чтобы продолжить)")
            robot.motion.simple_joystick()
            print("✅ Джойстик закрыт.")

            # 4. Получаем текущую позицию ЦТИ (TCP) в градусах
            pose = robot.motion.get_actual_position(
                position_format="tcp",
                orientation_units="deg"  # градусы — удобнее для человека
            )
            timestamp = datetime.now()

            # 5. Записываем в файл с меткой времени
            line = format_pose_for_file(pose, timestamp)
            with output_file.open("a", encoding="utf-8") as f:
                f.write(line)
            
            print(f"📍 Точка сохранена: {line.strip()}")
            print(f"📁 Файл: {output_file}\n")

    except KeyboardInterrupt:
        print("\n⚠️ Прервано пользователем (Ctrl+C)")
    except Exception as e:
        print(f"\n❌ Ошибка: {type(e).__name__}: {e}")
        raise
    finally:
        # Гарантированное отключение
        if robot.is_connected():
            robot.disconnect()
            print("🔌 Отключено от робота")


if __name__ == "__main__":
    main()