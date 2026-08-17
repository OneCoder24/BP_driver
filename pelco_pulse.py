"""
Минимальный тест чтения пульта SR-RC02 из ПО «Импульс»
Все логи пишутся в файл /tmp/pelco_log.txt
Переменные в Импульсе: status (str), packet_hex (str)
"""
import serial
from time import sleep, strftime
from API.tools import load_impulse_vars, save_impulse_vars, send_error_to_impulse

COM_PORT = "/dev/ttyUSB0"
BAUDRATE = 9600
LOG_FILE = "/tmp/pelco_log.txt"

# Аннотации — эти переменные должны быть созданы в Импульсе
status: str = "init"
packet_hex: str = ""


def log(msg: str):
    """Записывает сообщение в лог-файл с временной меткой."""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{strftime('%H:%M:%S')}] {msg}\n")
    except Exception as e:
        # Если не можем писать в лог — хотя бы попробуем отправить в Импульс
        try:
            send_error_to_impulse(f"Ошибка записи в лог: {e}")
        except Exception:
            pass
        sleep(0.1)


# Очищаем лог при старте
try:
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write(f"=== Старт скрипта {strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    log("Скрипт запущен")
except Exception as e:
    #send_error_to_impulse(f"Не удалось создать лог-файл: {e}")
    sleep(0.1)

try:
    load_impulse_vars()
    log(f"Переменные загружены: status={status}, packet_hex={packet_hex}")
    
    log(f"Пытаюсь открыть порт {COM_PORT} @ {BAUDRATE}")
    seri = serial.Serial(COM_PORT, BAUDRATE, timeout=0.1)
    status = "connected"
    log(f"Порт открыт успешно")
    save_impulse_vars()
except Exception as e:
    log(f"ОШИБКА открытия порта: {e}")
    #send_error_to_impulse(f"Не удалось открыть порт: {e}")

try:
    log("Вхожу в основной цикл")
    iteration = 0
    while True:
        load_impulse_vars()
        iteration += 1
        
        # Логируем каждую 100-ю итерацию, чтобы не засорять файл
        if iteration % 100 == 0:
            log(f"Итерация {iteration}, in_waiting={seri.in_waiting}")
        
        if seri.in_waiting >= 7:
            pkt = seri.read(7)
            if sum(pkt[1:6]) & 0xFF == pkt[6]:
                packet_hex = " ".join(f"{b:02X}" for b in pkt)
                status = "ok"
                log(f"Пакет: {packet_hex}")
            else:
                status = "bad_checksum"
                log(f"Битый пакет: {pkt.hex()}")
        
        save_impulse_vars()
        sleep(0.1)

except KeyboardInterrupt:
    status = "stopped"
    log("Остановлено пользователем")
    save_impulse_vars()
except Exception as e:
    log(f"КРИТИЧЕСКАЯ ОШИБКА: {e}")
    import traceback
    log(traceback.format_exc())
    try:
        #send_error_to_impulse(f"Ошибка: {e}")
        sleep(0.1)
    except Exception:
        pass
finally:
    log("Завершение работы")
    if 'seri' in locals() and seri.is_open:
        seri.close()
        log("Порт закрыт")