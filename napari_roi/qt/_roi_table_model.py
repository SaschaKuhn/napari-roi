from typing import Any, MutableSequence, Optional, Sequence

from qtpy.QtCore import QAbstractTableModel, QModelIndex, QObject, Qt

from .. import ROIBase


class ROITableModel(QAbstractTableModel):
    def __init__(
        self, rois: MutableSequence[ROIBase], parent: Optional[QObject] = None
    ) -> None:
        super(ROITableModel, self).__init__(parent=parent)
        self._rois = rois

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return len(self._rois)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.isValid():
            return 0
        return 7

    def data(
        self,
        index: QModelIndex,
        role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if 0 <= index.row() < self.rowCount() and role in (
            Qt.ItemDataRole.DisplayRole,
            Qt.ItemDataRole.EditRole,
        ):
            row = self._rois[index.row()]
            columns = (
                lambda: row.name,
                lambda: row.area,
                lambda: row.perimeter,
                lambda: row.length,
                lambda: row.major_axis,
                lambda: row.minor_axis,
                lambda: row.circularity,
            )
            if 0 <= index.column() < len(columns):
                return columns[index.column()]()
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: Qt.ItemDataRole = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            u = getattr(self._rois, "unit", "px")
            headers = ("name", 
                        f"area ({u}²)", f"perimeter ({u})", f"length ({u})",
                        f"major_axis ({u})", f"minor_axis ({u})", "circularity")
            return headers[section]
        return None

    def setData(
        self,
        index: QModelIndex,
        value: Any,
        role: Qt.ItemDataRole = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if (
            0 <= index.row() < self.rowCount()
            and role == Qt.ItemDataRole.EditRole
            and index.column() == 0
        ):
            str_value = str(value).strip()
            if len(str_value) > 0 and not any(
                roi.name == str_value and i != index.row()
                for i, roi in enumerate(self._rois)
            ):
                self._rois[index.row()].name = str_value
                self.dataChanged.emit(
                    self.createIndex(index.row(), 0),
                    self.createIndex(index.row(), 0),
                    [Qt.ItemDataRole.EditRole],
                )
                return True
        return False

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if (
            0 <= index.row() < self.rowCount()
            and 0 <= index.column() < self.columnCount()
        ):
            flags = (
                Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemNeverHasChildren
            )
            if index.column() == 0:
                flags |= Qt.ItemFlag.ItemIsEditable
            return flags

    # def insertRows(
    #     self, row: int, count: int, parent: QModelIndex = QModelIndex()
    # ) -> bool:
    #     if 0 <= row <= self.rowCount() and count > 0 and not parent.isValid():
    #         self.beginInsertRows(parent, row, row + count - 1)
    #         for i in range(row, row + count):
    #             self._rois.insert(i, ROI())
    #         self.endInsertRows()
    #         return True
    #     return False

    def removeRows(
        self, row: int, count: int, parent: QModelIndex = QModelIndex()
    ) -> bool:
        if 0 <= row < row + count <= self.rowCount() and not parent.isValid():
            self.beginRemoveRows(parent, row, row + count - 1)
            for i in range(row, row + count):
                del self._rois[row]
            self.endRemoveRows()
            return True
        return False

    def refresh_rows(self, row_indices: Sequence[int]) -> None:
        for row_index in row_indices:
            self.dataChanged.emit(
                self.createIndex(row_index, 0),
                self.createIndex(row_index, self.columnCount() - 1),
                [Qt.ItemDataRole.DisplayRole],
            )

    def reset(self) -> None:
        self.beginResetModel()
        self.endResetModel()

    @property
    def rois(self) -> MutableSequence[ROIBase]:
        return self._rois
