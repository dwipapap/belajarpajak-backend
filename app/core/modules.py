"""Module catalog, dependencies, and effective tenant module/settings resolution."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.enums import AssignmentDocType, TaxReturnType
from app.models.tenant import Tenant


@dataclass(frozen=True)
class ModuleInfo:
    key: str
    label: str
    group: str
    depends_on: tuple[str, ...] = ()
    dependency_hint: str | None = None


MODULE_CATALOG: tuple[ModuleInfo, ...] = (
    # e-Faktur
    ModuleInfo(
        key="faktur_keluaran",
        label="e-Faktur Pajak Keluaran",
        group="e-Faktur",
    ),
    ModuleInfo(
        key="faktur_masukan",
        label="e-Faktur Pajak Masukan",
        group="e-Faktur",
    ),
    ModuleInfo(
        key="retur",
        label="Retur Faktur Pajak",
        group="e-Faktur",
        depends_on=("faktur_masukan",),
        dependency_hint="Retur Faktur Pajak butuh e-Faktur Pajak Masukan",
    ),
    # eBupot
    ModuleInfo(
        key="bppu",
        label="e-Bupot BPPU (PPh Unifikasi)",
        group="eBupot",
    ),
    ModuleInfo(
        key="bp21",
        label="e-Bupot BP 21 (Selain Pegawai Tetap)",
        group="eBupot",
    ),
    ModuleInfo(
        key="bp26",
        label="e-Bupot BP 26 (Wajib Pajak Luar Negeri)",
        group="eBupot",
    ),
    ModuleInfo(
        key="bpnr",
        label="e-Bupot BPNR (Non-Residen)",
        group="eBupot",
    ),
    ModuleInfo(
        key="bpmp",
        label="e-Bupot BPMP & BP A1/A2 (Pegawai Tetap)",
        group="eBupot",
    ),
    ModuleInfo(
        key="sp",
        label="e-Bupot Penyetoran Sendiri (SP)",
        group="eBupot",
    ),
    ModuleInfo(
        key="cy",
        label="e-Bupot Pemotongan Digunggung (CY)",
        group="eBupot",
    ),
    # SPT
    ModuleInfo(
        key="spt_ppn",
        label="SPT Masa PPN",
        group="SPT",
        depends_on=("faktur_keluaran", "faktur_masukan"),
        dependency_hint="SPT Masa PPN butuh e-Faktur Pajak Keluaran atau Masukan",
    ),
    ModuleInfo(
        key="spt_pph_unifikasi",
        label="SPT Masa PPh Unifikasi",
        group="SPT",
        depends_on=("bppu", "bpnr", "sp", "cy", "bp26"),
        dependency_hint=(
            "SPT Masa PPh Unifikasi butuh minimal satu modul "
            "e-Bupot Unifikasi (BPPU/BPNR/SP/CY/BP26)"
        ),
    ),
    ModuleInfo(
        key="spt_pph21_26",
        label="SPT Masa PPh 21/26",
        group="SPT",
        depends_on=("bp21", "bpmp"),
        dependency_hint="SPT Masa PPh 21/26 butuh e-Bupot BP 21 atau BPMP",
    ),
    # Pembayaran
    ModuleInfo(
        key="deposit",
        label="Deposit Pajak",
        group="Pembayaran",
    ),
    ModuleInfo(
        key="pbk",
        label="Pemindahbukuan (Pbk)",
        group="Pembayaran",
    ),
    ModuleInfo(
        key="tagihan",
        label="Tagihan Pajak (SKP/STP)",
        group="Pembayaran",
    ),
    ModuleInfo(
        key="angsuran",
        label="Angsuran Pembayaran",
        group="Pembayaran",
    ),
    # Kelas
    ModuleInfo(
        key="tugas",
        label="Tugas & Penilaian Kelas",
        group="Kelas",
    ),
)

ALL_MODULE_KEYS: tuple[str, ...] = tuple(m.key for m in MODULE_CATALOG)
ALL_MODULE_SET: frozenset[str] = frozenset(ALL_MODULE_KEYS)
MODULE_BY_KEY: dict[str, ModuleInfo] = {m.key: m for m in MODULE_CATALOG}

DEFAULT_SETTINGS: dict[str, bool] = {
    "show_auto_check_details": True,
    "allow_late_submission": True,
    "allow_resubmission": True,
}


def module_label(key: str) -> str:
    info = MODULE_BY_KEY.get(key)
    return info.label if info else key


def resolve_allowed_modules(tenant: Tenant | None) -> list[str]:
    """Return ordered list of allowed module keys for a tenant (NULL = all)."""
    if tenant is None or tenant.allowed_modules is None:
        return list(ALL_MODULE_KEYS)
    allowed_set = set(tenant.allowed_modules) & ALL_MODULE_SET
    return [k for k in ALL_MODULE_KEYS if k in allowed_set]


def resolve_enabled_modules(tenant: Tenant | None) -> list[str]:
    """Return ordered list of admin-selected enabled modules (NULL = all allowed)."""
    allowed = resolve_allowed_modules(tenant)
    if tenant is None or tenant.enabled_modules is None:
        return allowed
    enabled_set = set(tenant.enabled_modules) & ALL_MODULE_SET
    return [k for k in ALL_MODULE_KEYS if k in enabled_set]


def resolve_effective_modules(tenant: Tenant | None) -> list[str]:
    """Effective modules = allowed ∩ enabled, preserving catalog order."""
    allowed_set = set(resolve_allowed_modules(tenant))
    if tenant is None or tenant.enabled_modules is None:
        return [k for k in ALL_MODULE_KEYS if k in allowed_set]
    enabled_set = set(tenant.enabled_modules) & allowed_set
    return [k for k in ALL_MODULE_KEYS if k in enabled_set]


def resolve_tenant_settings(tenant: Tenant | None) -> dict[str, bool]:
    """Return merged learning settings with defaults."""
    merged = dict(DEFAULT_SETTINGS)
    if tenant is not None and isinstance(tenant.settings, dict):
        for k in DEFAULT_SETTINGS:
            if k in tenant.settings and isinstance(tenant.settings[k], bool):
                merged[k] = tenant.settings[k]
    return merged


def spt_return_type_to_module(return_type: TaxReturnType | str) -> str:
    """Map TaxReturnType to module key."""
    val = getattr(return_type, "value", str(return_type))
    if val == "ppn":
        return "spt_ppn"
    if val == "pph_unifikasi":
        return "spt_pph_unifikasi"
    if val in ("pph_21_26", "pph21_26"):
        return "spt_pph21_26"
    return "spt_ppn"


def enabled_spt_return_types(effective_modules: set[str] | list[str]) -> list[TaxReturnType]:
    """Return list of TaxReturnType enum values enabled by effective_modules."""
    eff = set(effective_modules)
    types: list[TaxReturnType] = []
    if "spt_ppn" in eff:
        types.append(TaxReturnType.ppn)
    if "spt_pph_unifikasi" in eff:
        types.append(TaxReturnType.pph_unifikasi)
    if "spt_pph21_26" in eff:
        types.append(TaxReturnType.pph_21_26)
        types.append(TaxReturnType.pph21_26)
    return types


def assignment_doc_type_to_module(doc_type: AssignmentDocType | str) -> str:
    """Map AssignmentDocType to module key."""
    val = getattr(doc_type, "value", str(doc_type))
    if val in ("spt_ppn",):
        return "spt_ppn"
    if val in ("spt_pph_unifikasi",):
        return "spt_pph_unifikasi"
    if val in ("spt_pph_21_26", "spt_pph21_26"):
        return "spt_pph21_26"
    # bp21, bp26, bppu, bpmp, bpnr, sp, cy, faktur_keluaran, faktur_masukan match 1:1
    return val


def enabled_assignment_doc_types(effective_modules: set[str] | list[str]) -> list[str]:
    """Return list of AssignmentDocType string values whose target module is enabled."""
    eff = set(effective_modules)
    out: list[str] = []
    for dt in AssignmentDocType:
        if assignment_doc_type_to_module(dt) in eff:
            out.append(dt.value)
    return out
