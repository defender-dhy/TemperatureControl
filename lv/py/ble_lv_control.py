import threading, asyncio, uuid, logging, os
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

# 全局可变 BLE UUID 与 VI 路径（由 start_ble_service 传入）
SERVICE_UUID = None
TX_CHAR_UUID = None
RX_CHAR_UUID = None
VI_PATH = None

# 全局状态
_loop = None               # asyncio event loop
_provider = None           # GATT service provider
_thread = None             # BLE service thread
_vi = None                 # LabVIEW VI reference
_tx_char = None            # GATT local characteristic for TX notifications

# 通信回调：写请求
def _on_write(sender, args):
    op = args.get_request_async()
    def completed(async_info, _):
        req = async_info.get_results()
        reader = DataReader.from_buffer(req.value)
        data_list = []
        while reader.unconsumed_buffer_length > 0:
            b = reader.read_byte()
            data_list.append(b)
        data = bytes(data_list)
        logging.info(f"收到手机写入: {data.hex()} ({data!r})")
        try:
            req.respond()
        except Exception as e:
            logging.error(f"确认写入失败：{e}")
    op.completed = completed

# 初始化 GATT Service & 特征
async def _setup_gatt():
    global _provider, _tx_char
    res = await GattServiceProvider.create_async(SERVICE_UUID)
    if res.error:
        raise RuntimeError(f"GATT Service 创建失败: {res.error}")
    _provider = res.service_provider
    svc = _provider.service

    # 创建 TX 特征（READ + NOTIFY）并保存引用
    rd = GattLocalCharacteristicParameters()
    rd.characteristic_properties = (
        GattCharacteristicProperties.READ |
        GattCharacteristicProperties.NOTIFY
    )
    rd.user_description = "PC->Phone TX"
    w = DataWriter(); w.write_string("Ready"); rd.static_value = w.detach_buffer()
    tx_res = await svc.create_characteristic_async(TX_CHAR_UUID, rd)
    if tx_res.error:
        raise RuntimeError(f"TX 特征创建失败: {tx_res.error}")
    _tx_char = tx_res.characteristic

    # 创建 RX 特征（WRITE）并绑定写回调
    wr = GattLocalCharacteristicParameters()
    wr.characteristic_properties = (
        GattCharacteristicProperties.WRITE |
        GattCharacteristicProperties.WRITE_WITHOUT_RESPONSE
    )
    wr.user_description = "Phone->PC RX"
    rx_res = await svc.create_characteristic_async(RX_CHAR_UUID, wr)
    if rx_res.error:
        raise RuntimeError(f"RX 特征创建失败: {rx_res.error}")
    rx_res.characteristic.add_write_requested(_on_write)

# 广播参数
def _adv_params():
    p = GattServiceProviderAdvertisingParameters()
    p.is_connectable = True
    p.is_discoverable = True
    return p

# 启动 BLE 服务
def start_ble_service(
    service_uuid_str: str,
    tx_char_uuid_str: str,
    rx_char_uuid_str: str,
    vi_path: str
) -> bool:
    global SERVICE_UUID, TX_CHAR_UUID, RX_CHAR_UUID, VI_PATH, _loop, _thread, _vi

    # 解析与赋值参数
    SERVICE_UUID = uuid.UUID(service_uuid_str)
    TX_CHAR_UUID = uuid.UUID(tx_char_uuid_str)
    RX_CHAR_UUID = uuid.UUID(rx_char_uuid_str)
    VI_PATH = vi_path

    # 清空日志
    try:
        open(log_path, "w", encoding="utf-8").close()
    except Exception as e:
        logging.error(f"清空日志文件失败: {e}")

    # 初始化 LabVIEW COM VI
    lv = win32com.client.Dispatch("LabVIEW.Application")
    _vi = lv.GetVIReference(VI_PATH)

    # 后台线程运行 BLE 服务
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

# 发送通知到手机
def send_message(message: str) -> bool:
    """通过 TX 特征异步向手机发送字符串消息"""
    global _tx_char, _loop
    if not _tx_char or not _loop:
        logging.error("TX 特征未初始化或事件循环尚未启动")
        return False
    try:
        # 构造缓冲区
        buf_writer = DataWriter()
        buf_writer.write_string(message)
        buf = buf_writer.detach_buffer()
        # 定义实际的协程包装器
        async def _notify():
            # WinRT async 方法需要直接 await
            await _tx_char.notify_value_async(buf)
        # 在事件循环线程里调度该协程
        asyncio.run_coroutine_threadsafe(_notify(), _loop)
        logging.info(f"发送通知到手机: {message}")
        return True
    except Exception as e:
        logging.error(f"发送通知失败: {e}")
        return False

