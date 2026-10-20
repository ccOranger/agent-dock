"""可选：把 IT 示例 FAQ 真正写入知识库（需要 embedding 服务可用）

seed_data.py 只在 documents 表写归档行，不产生向量；本脚本通过
DocumentService.create_entry 把示例 FAQ 写入 Qdrant，供 it_knowledge /
it_incident 检索。单条失败只打印原因并继续，不阻断其余条目。

用法（backend 目录，配好 .env 后）：
    .\\.venv\\Scripts\\python.exe ..\\scripts\\seed_it_kb.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
os.environ.setdefault("PYTHONPATH", "backend")

from app.models.repositories import DocumentRepo  # noqa: E402
from app.services.document_service import DocumentService  # noqa: E402

IT_FAQ_DOCS = [
    (
        "it_default",
        "IT 服务台常见问题 FAQ",
        """# IT 服务台常见问题 FAQ

## 如何重置域账号密码
1. 打开企业自助服务门户，选择“账号与密码 - 重置密码”。
2. 通过手机验证码或邮箱验证身份后设置新密码。
3. 新密码需包含大小写字母、数字与符号，长度不少于 12 位。
4. 修改后重新登录办公电脑与邮箱；连续 5 次输错会锁定 30 分钟。

## 如何申请 VPN 权限
1. 在 IT 服务台提交“VPN 权限申请”，选择使用场景与期限。
2. 需直属主管审批，审批通过后 1 个工作日内开通。
3. 开通后使用企业账号登录 VPN 客户端，禁止共享账号或密码。
4. 离职或项目结束时权限自动回收。

## 如何安装办公软件
- 公司软件中心已上架的软件可自助安装，无需审批。
- 未上架软件需提交“软件安装申请”，说明用途与 License 来源。
- 禁止安装来源不明的破解软件与个人版网盘工具。

## 共享盘权限如何申请
1. 在 IT 服务台提交“共享盘权限申请”，填写共享路径与所需权限（只读/读写）。
2. 由目录负责人审批后生效，通常 1 个工作日内完成。
3. 权限按最小化原则授予，项目结束后请主动申请回收。

## 报修后多久能处理
- 工单受理后会按影响范围分级：影响多人的网络/系统故障优先处理。
- 一般问题在 1 个工作日内联系；需要备件或现场处理的会另行约定时间。
- 可在 IT 服务台凭工单号查询处理进度。
""",
    ),
    (
        "it_network",
        "办公网络与 VPN 故障排查手册",
        """# 办公网络故障排查手册

## 无法连接 Wi-Fi
1. 确认笔记本 Wi-Fi 开关已打开，并选择公司 SSID（员工网络）。
2. 忘记网络后重新连接，使用域账号登录认证。
3. 若提示认证失败，先确认域账号密码是否过期或被锁定。
4. 同一楼层多人同时无法连接时，按网络故障报修并说明楼层与影响人数。

## 有线网络无法上网
1. 检查网线是否插紧，网口指示灯是否闪烁。
2. 尝试更换网线或工位网口，排除线缆与端口问题。
3. 确认 IP 获取方式为自动（DHCP），不要手工填写陌生网段地址。
4. 仍无法上网时提交报修，附上工位号与网口编号。

## VPN 连接失败
1. 确认当前网络可访问互联网，再启动 VPN 客户端。
2. 检查账号是否已开通 VPN 权限、是否在有效期内。
3. 提示证书错误时，在客户端重新下载配置或联系 IT 更新证书。
4. 连接成功但无法访问内网时，确认目标地址属于内网网段并重试。

## 打印机无法连接
1. 确认打印机电源与网络指示灯正常。
2. 在“设备和打印机”中删除旧队列，重新添加网络打印机。
3. 公共打印机显示脱机时，先检查是否缺纸、卡纸或缺墨。
4. 多人同时无法打印时按设备故障报修，并提供打印机编号。
""",
    ),
    (
        "it_assets",
        "IT 资产领用与保修说明",
        """# IT 资产领用与保修说明

## 如何查询资产信息
- 每台公司设备都有唯一资产编号（如 IT-LT-1001）与序列号（SN）。
- 可在 IT 服务台提供资产编号、序列号或领用人，查询型号、保修期与网络配置。
- 资产编号通常贴在设备底部或电池仓附近的标签上。

## 资产领用流程
1. 在 IT 服务台提交“资产领用申请”，选择设备类型与用途。
2. 直属主管审批后，由 IT 资产管理员分配设备并登记领用记录。
3. 领用时现场核对型号、序列号与配件，确认无误后签收。
4. 设备仅限本人办公使用，不得转借或私自拆装。

## 保修与维修
- 保修期内的硬件故障由 IT 统一送修，个人不要自行联系厂商拆机。
- 人为损坏（进液、摔碰、私自拆装）不在保修范围，需按公司规定处理。
- 申请保修时提供资产编号、故障现象与购买/领用时间。

## 资产归还与报废
1. 离职或岗位调整时，需归还笔记本、显示器、配件与门禁卡。
2. 归还前自行备份个人文件，IT 将按安全规范清除数据。
3. 达到报废年限且无法维修的设备，由 IT 统一发起报废流程。
""",
    ),
]


async def main() -> int:
    service = DocumentService()
    created = 0
    failed = 0
    for namespace, title, content in IT_FAQ_DOCS:
        rows, _ = await DocumentRepo.list_all(namespace=namespace, page=1, page_size=100)
        if any(d.title == title for d in rows):
            print(f"跳过（已存在）: [{namespace}] {title}")
            continue
        try:
            await service.create_entry(
                namespace=namespace,
                title=title,
                content=content,
                uploaded_by=None,
            )
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"失败（可稍后重试）: [{namespace}] {title} -> {type(e).__name__}: {e}")
            continue
        created += 1
        print(f"OK: [{namespace}] {title}")

    print(f"完成：新增 {created} 条，失败 {failed} 条")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
