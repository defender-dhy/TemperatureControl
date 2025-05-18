import threading, asyncio, uuid, logging, os, struct
from winrt.windows.devices.bluetooth.genericattributeprofile import (
    GattServiceProvider,
    GattLocalCharacteristicParameters,
    GattServiceProviderAdvertisingParameters,
    GattCharacteristicProperties,
)
from winrt.windows.storage.streams import DataWriter, DataReader
import win32com.client

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
VI_PATH = None

# 全局状态
_loop = None               # asyncio event loop
_provider = None           # GATT service provider
_thread = None             # BLE service thread
_vi = None                 # LabVIEW VI reference
_normal_char = None        # 普通数据特征
_alarm_char = None         # 报警数据特征
_control_char = None       # 控制命令特征
_fusion_char = None        # 融合设置特征

# 通信回调：各特征写请求（目前实现相同逻辑）
def _on_write_normal(sender, args):
    _generic_write_handler(sender, args)

def _on_write_alarm(sender, args):
    _generic_write_handler(sender, args)

def _on_write_control(sender, args):
    _generic_write_handler(sender, args)

def _on_write_fusion(sender, args):
    _generic_write_handler(sender, args)

# 通用写请求处理
def _generic_write_handler(sender, args):
    op = args.get_request_async()
    def completed(async_info, _):
        req = async_info.get_results()
        reader = DataReader.from_buffer(req.value)
        data = bytes(reader.read_bytes(reader.unconsumed_buffer_length))
        logging.info(f"[{sender.user_description}] 收到写入: {data.hex()} ({data!r})")
        try:
            req.respond()
        except Exception as e:
            logging.error(f"确认写入失败：{e}")
    op.completed = completed

# 初始化 GATT Service & 特征
async def _setup_gatt():
    global _provider, _normal_char, _alarm_char, _control_char, _fusion_char
    res = await GattServiceProvider.create_async(SERVICE_UUID)
    if res.error:
        raise RuntimeError(f"GATT Service 创建失败: {res.error}")
    _provider = res.service_provider
    svc = _provider.service

    def make_params(desc, props, init_bytes=None):
        p = GattLocalCharacteristicParameters()
        p.characteristic_properties = props
        p.user_description = desc
        if init_bytes is not None:
            # 初始化 static_value 为与 init_bytes 相同的数据（全零或指定内容）
            w = DataWriter()
            w.write_bytes(list(init_bytes))
            p.static_value = w.detach_buffer()
        return p

    # 1. 普通数据特征 (NormalDataChar): READ only
    params = make_params("NormalData", GattCharacteristicProperties.READ, struct.pack('<d', 0.0))
    n_res = await svc.create_characteristic_async(NORMAL_CHAR_UUID, params)
    if n_res.error:
        raise RuntimeError(f"NormalData 特征创建失败: {n_res.error}")
    _normal_char = n_res.characteristic
    # 如果应用写入测试，可绑定
    _normal_char.add_write_requested(_on_write_normal)

    # 2. 报警数据特征 (AlarmData): READ only
    params = make_params("AlarmData", GattCharacteristicProperties.READ, struct.pack('<d', 0.0))
    a_res = await svc.create_characteristic_async(ALARM_CHAR_UUID, params)
    if a_res.error:
        raise RuntimeError(f"AlarmData 特征创建失败: {a_res.error}")
    _alarm_char = a_res.characteristic
    _alarm_char.add_write_requested(_on_write_alarm)

    # 3. 控制命令特征 (ControlCmd): WRITE only
    params = make_params("ControlCmd", GattCharacteristicProperties.WRITE_WITHOUT_RESPONSE, None)
    c_res = await svc.create_characteristic_async(CONTROL_CHAR_UUID, params)
    if c_res.error:
        raise RuntimeError(f"ControlCmd 特征创建失败: {c_res.error}")
    _control_char = c_res.characteristic
    _control_char.add_write_requested(_on_write_control)

    # 4. 融合设置特征 (FusionSettings): WRITE only
    params = make_params("FusionSettings", GattCharacteristicProperties.WRITE_WITHOUT_RESPONSE, None)
    f_res = await svc.create_characteristic_async(FUSION_CHAR_UUID, params)
    if f_res.error:
        raise RuntimeError(f"FusionSettings 特征创建失败: {f_res.error}")
    _fusion_char = f_res.characteristic
    _fusion_char.add_write_requested(_on_write_fusion)

# 广播参数
def _adv_params():
    p = GattServiceProviderAdvertisingParameters()
    p.is_connectable = True
    p.is_discoverable = True
    return p
    
# 启动 BLE 服务
def start_ble_service(
    vi_path: str
) -> bool:
    global SERVICE_UUID, NORMAL_CHAR_UUID, ALARM_CHAR_UUID, CONTROL_CHAR_UUID, FUSION_CHAR_UUID, VI_PATH, _loop, _thread, _vi

    VI_PATH = vi_path

    # 清空日志
    try:
        open(log_path, "w", encoding="utf-8").close()
    except Exception as e:
        logging.error(f"清空日志文件失败: {e}")

    # 初始化 LabVIEW COM VI
    lv = win32com.client.Dispatch("LabVIEW.Application")
    _vi = lv.GetVIReference(VI_PATH)

    def runner():
        global _loop
        try:
            loop = asyncio.new_event_loop()
            _loop = loop
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_setup_gatt())
            _provider.start_advertising(_adv_params())
            logging.info("BLE 广播已启动")
            loop.run_forever()
        except Exception as e:
            logging.error(f"服务线程异常退出: {e}")

    _thread = threading.Thread(target=runner, daemon=True)
    _thread.start()
    return True

# 停止 BLE 服务
def stop_ble_service() -> bool:
    global _provider, _loop, _thread
    if _provider:
        _provider.stop_advertising()
    if _loop:
        _loop.call_soon_threadsafe(_loop.stop)
    if _thread:
        _thread.join(timeout=2)
    logging.info("BLE 服务已停止")
    return True

# 读取日志
def read_log() -> str:
    try:
        return open(log_path, "r", encoding="utf-8").read()
    except Exception as e:
        logging.error(f"读取日志失败: {e}")
        return ""

# 通过 WRITE 更新普通数据特征的值
async def _write_char_value(char, data: bytes):
    buf = DataWriter()
    buf.write_bytes(list(data))
    await char.write_value_async(buf.detach_buffer())
    logging.info(f"写入 {char.user_description} 值: {data.hex()}")

# 发送普通数据
def send_normal_data(temp: float) -> bool:
    """向 NormalData 写入当前温度 (double, 8 B)"""
    if not _normal_char or not _loop:
        logging.error("普通特征未初始化或事件循环尚未启动")
        return False
    data = struct.pack('<d', temp)
    asyncio.run_coroutine_threadsafe(_write_char_value(_normal_char, data), _loop)
    return True

# 发送报警数据
def send_alarm_data(alarm_temp: float, alarm_time: str, alarm_type: str) -> bool:
    """向 AlarmData 写入告警数据 (32 B: double, ASCII 时间16 B, UTF-16BE类型8 B)"""
    if not _alarm_char or not _loop:
        logging.error("报警特征未初始化或事件循环尚未启动")
        return False
    buf = struct.pack('<d', alarm_temp)
    time_bytes = alarm_time.encode('ascii').ljust(16, b'\x00')[:16]
    type_bytes = alarm_type.encode('utf-16-be').ljust(8, b'\x00')[:8]
    asyncio.run_coroutine_threadsafe(
        _write_char_value(_alarm_char, buf + time_bytes + type_bytes), _loop)
    return True
                                                  
if __name__ == "__main__":
    import time
    vi_path = r"E:\_team_proj\temp_control\my_labview\my_labview_projs\TemperatureControl\lv\bt_test3_struct_data.vi"

    # 启动 BLE 服务
    if start_ble_service(vi_path):
        print("BLE service started. Press Ctrl+C to stop.")
        # 等待服务完成初始化
        time.sleep(2)

        # ====== 测试发送 ======
        # 1) 发送一次常态温度
        temp = 25.0
        print(f"Sending normal data: {temp}")
        send_normal_data(temp)

        # 2) 发送一次告警数据
        alarm_temp = 75.5
        alarm_time = time.strftime("%Y%m%d%H%M%S")
        alarm_type = "WARN"
        print(f"Sending alarm data: temp={alarm_temp}, time={alarm_time}, type={alarm_type}")
        send_alarm_data(alarm_temp, alarm_time, alarm_type)

        # 3) 进入循环，每隔10秒更新常态温度
        try:
            while True:
                time.sleep(10)
                temp += 0.5
                print(f"Refreshing normal data: {temp}")
                send_normal_data(temp)
        except KeyboardInterrupt:
            pass  # fall through to shutdown

        # 停止服务
        stop_ble_service()
        print("BLE service stopped.")
    else:
        print("Failed to start BLE service.")