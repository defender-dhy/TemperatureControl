import threading, asyncio, uuid, logging, os, struct, time
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattServiceProvider,
    GattLocalCharacteristicParameters,
    GattServiceProviderAdvertisingParameters,
    GattCharacteristicProperties,
)
from winrt.windows.storage.streams import DataWriter, DataReader
import win32com.client

# 常量
NULL = b"\x00"  # 单字节 0，用于字符串填充

# 日志文件
log_path = os.path.join(os.path.dirname(__file__), "ble_lv_control.log")
file_handler = logging.FileHandler(log_path, encoding="utf-8")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(message)s")
logging.getLogger().addHandler(file_handler)

# 固定 BLE UUID
SERVICE_UUID        = uuid.UUID("12345678-1234-5678-1234-56789abcdef0")
NORMAL_CHAR_UUID    = uuid.UUID("12345678-1234-5678-1234-56789abc0001")
ALARM_CHAR_UUID     = uuid.UUID("12345678-1234-5678-1234-56789abc0002")
CONTROL_CHAR_UUID   = uuid.UUID("12345678-1234-5678-1234-56789abc0003")
FUSION_CHAR_UUID    = uuid.UUID("12345678-1234-5678-1234-56789abc0004")

# 全局状态
_loop = None
_provider = None
_thread = None
_vi = None
_normal_char = None
_alarm_char = None
_control_char = None
_fusion_char = None

# ---------- 回调 ----------

# ControlCmd 回调：1 字节 cmd
#   0x01 = 启动温控
#   0x02 = 停止温控
async def _handle_control_cmd(cmd: int):
    if cmd == 0x01:
        logging.info("[ControlCmd] 启动温控")
        # TODO: 调用 LabVIEW 或内部逻辑启动
    elif cmd == 0x02:
        logging.info("[ControlCmd] 停止温控")
        # TODO: 调用 LabVIEW 或内部逻辑停止
    else:
        logging.warning(f"[ControlCmd] 未识别指令: 0x{cmd:02X}")


def _on_write_control(sender, args):
    op = args.get_request_async()
    def completed(async_info, _):
        req = async_info.get_results()
        reader = DataReader.from_buffer(req.value)
        if reader.unconsumed_buffer_length >= 1:
            cmd = reader.read_byte()
            logging.info(f"[ControlCmd] 收到 cmd=0x{cmd:02X}")
            asyncio.run_coroutine_threadsafe(_handle_control_cmd(cmd), _loop)
        try:
            req.respond()
        except Exception as e:
            logging.error(f"ControlCmd 确认失败: {e}")
    op.completed = completed

# FusionSettings 回调：可变长度 Write Long
async def _handle_fusion_settings(cmd: int, doubles: list[float]):
    if cmd == 0x01 and len(doubles) == 3:
        setpoint, warn_high, warn_low = doubles
        logging.info(f"[FusionSettings] 保存温控设置: {doubles}")
        # TODO: 处理温控设置
    elif cmd == 0x02 and len(doubles) == 8:
        logging.info(f"[FusionSettings] 保存 PID 设置: {doubles}")
        # TODO: 处理 PID 设置
    else:
        logging.warning(f"[FusionSettings] 数据格式错误 cmd=0x{cmd:02X} doubles={doubles}")


def _on_write_fusion(sender, args):
    op = args.get_request_async()
    def completed(async_info, _):
        req = async_info.get_results()
        reader = DataReader.from_buffer(req.value)
        if reader.unconsumed_buffer_length >= 1:
            cmd = reader.read_byte()
            # 剩余字节转成 double 列表（每 8 字节）
            doubles = []
            while reader.unconsumed_buffer_length >= 8:
                raw = reader.read_bytes(8)
                doubles.append(struct.unpack('<d', bytes(raw))[0])
            logging.info(f"[FusionSettings] 收到 cmd=0x{cmd:02X} doubles={doubles}")
            asyncio.run_coroutine_threadsafe(_handle_fusion_settings(cmd, doubles), _loop)
        try:
            req.respond()
        except Exception as e:
            logging.error(f"FusionSettings 确认失败: {e}")
    op.completed = completed


def _on_sub_changed(sender, args):
    count = getattr(sender.subscribed_clients, 'size', 0)
    logging.info(f"{sender.user_description} 订阅客户端数量: {count}")

# ---------- GATT 初始化 ----------
async def _setup_gatt():
    global _provider, _normal_char, _alarm_char, _control_char, _fusion_char

    res = await GattServiceProvider.create_async(SERVICE_UUID)
    if res.error:
        raise RuntimeError(f"GATT Service 创建失败: {res.error}")
    _provider = res.service_provider
    svc = _provider.service

    def make_params(desc: str, props: GattCharacteristicProperties, init_bytes: bytes = b""):
        p = GattLocalCharacteristicParameters()
        p.characteristic_properties = props
        p.user_description = desc
        if init_bytes:
            w = DataWriter()
            w.write_bytes(list(init_bytes))
            p.static_value = w.detach_buffer()
        return p

    # NormalDataChar  (READ + NOTIFY)
    n_params = make_params(
        "NormalData",
        GattCharacteristicProperties.READ | GattCharacteristicProperties.NOTIFY,
        struct.pack('<d', 0.0)
    )
    n_res = await svc.create_characteristic_async(NORMAL_CHAR_UUID, n_params)
    if n_res.error:
        raise RuntimeError(f"NormalData 特征创建失败: {n_res.error}")
    _normal_char = n_res.characteristic
    _normal_char.add_subscribed_clients_changed(_on_sub_changed)

    # AlarmDataChar (READ + NOTIFY)
    a_params = make_params(
        "AlarmData",
        GattCharacteristicProperties.READ | GattCharacteristicProperties.NOTIFY,
        bytes(32)  # 预留 32 字节
    )
    a_res = await svc.create_characteristic_async(ALARM_CHAR_UUID, a_params)
    if a_res.error:
        raise RuntimeError(f"AlarmData 特征创建失败: {a_res.error}")
    _alarm_char = a_res.characteristic
    _alarm_char.add_subscribed_clients_changed(_on_sub_changed)

    # ControlCmd (WRITE)
    c_params = make_params("ControlCmd", GattCharacteristicProperties.WRITE)
    c_res = await svc.create_characteristic_async(CONTROL_CHAR_UUID, c_params)
    _control_char = c_res.characteristic
    _control_char.add_write_requested(_on_write_control)

    # FusionSettings (WRITE)
    f_params = make_params("FusionSettings", GattCharacteristicProperties.WRITE)
    f_res = await svc.create_characteristic_async(FUSION_CHAR_UUID, f_params)
    _fusion_char = f_res.characteristic
    _fusion_char.add_write_requested(_on_write_fusion)

# ---------- 广播 ----------

def _adv_params():
    p = GattServiceProviderAdvertisingParameters()
    p.is_connectable = True
    p.is_discoverable = True
    return p

# ---------- 服务控制 ----------

def start_ble_service(vi_path: str) -> bool:
    global _loop, _thread, _vi

    # 清日志
    try:
        open(log_path, "w", encoding="utf-8").close()
    except Exception as e:
        logging.error(f"清空日志文件失败: {e}")

    # LabVIEW VI
    lv = win32com.client.Dispatch("LabVIEW.Application")
    _vi = lv.GetVIReference(vi_path)

    def runner():
        global _loop
        loop = asyncio.new_event_loop()
        _loop = loop
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_setup_gatt())
        _provider.start_advertising(_adv_params())
        logging.info("BLE 广播已启动")
        loop.run_forever()

    _thread = threading.Thread(target=runner, daemon=True)
    _thread.start()
    return True


def stop_ble_service():
    if _provider:
        _provider.stop_advertising()
    if _loop:
        _loop.call_soon_threadsafe(_loop.stop)
    if _thread:
        _thread.join(timeout=2)
    logging.info("BLE 服务已停止")

# ---------- 推送通知 ----------
async def _notify_char(char, payload: bytes):
    if getattr(char.subscribed_clients, 'size', 0) == 0:
        logging.info(f"{char.user_description}: 无订阅，跳过通知")
        return
    w = DataWriter(); w.write_bytes(list(payload)); buf = w.detach_buffer()
    try:
        await char.notify_value_async(buf)
        logging.info(f"推送 {char.user_description}: {payload.hex()}")
    except Exception as e:
        logging.error(f"推送异常: {e}")

# ---------- 发送接口 ----------

ALARM_TYPE_MAP = {
    "HIGH": 1,
    "LOW": 2,
}

def send_normal(temp: float):
    if _normal_char and _loop:
        data = struct.pack('<d', temp)
        asyncio.run_coroutine_threadsafe(_notify_char(_normal_char, data), _loop)


def send_alarm(temp: float, alarm_time: str, alarm_type: str):
    """向 AlarmData 推送告警: 8B double + 4B Unix 时间戳 + 1B 类型码 = 13 字节"""
    if _alarm_char and _loop:
        try:
            ts = int(time.mktime(time.strptime(alarm_time, "%Y%m%d%H%M%S")))
        except Exception:
            ts = int(time.time())
        type_code = ALARM_TYPE_MAP.get(alarm_type.upper(), 0)
        payload = struct.pack('<dIB', temp, ts, type_code)
        asyncio.run_coroutine_threadsafe(_notify_char(_alarm_char, payload), _loop)

# ---------- 读取日志 ----------
def read_log() -> str:
    try:
        return open(log_path, "r", encoding="utf-8").read()
    except Exception as e:
        logging.error(f"读取日志失败: {e}")
        return ""
    
# ---------- 主程序 ----------
if __name__ == "__main__":
    import time
    VI_PATH = r"E:\_team_proj\temp_control\my_labview\my_labview_projs\TemperatureControl\lv\bt_test3_struct_data.vi"
    start_ble_service(VI_PATH)
    time.sleep(2)

    send_normal(25.0)
    send_alarm(75.5, time.strftime("%Y%m%d%H%M%S"), "WARN")

    try:
        t = 25.0
        while True:
            time.sleep(10)
            t += 0.5
            send_normal(t)
            send_alarm(t, "2025-5-18 10:59", "High")
    except KeyboardInterrupt:
        stop_ble_service()