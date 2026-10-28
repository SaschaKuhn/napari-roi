import re
from pathlib import Path
from typing import TYPE_CHECKING, MutableSequence, Optional, Sequence

import json
import tifffile

import numpy as np
import pandas as pd
from napari.layers import Shapes
from napari.utils.events import Event
from napari.viewer import Viewer
from qtpy.QtCore import QEvent, QItemSelection, QObject, QPoint, QSignalBlocker, Qt
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QStyle,
    QTableView,
    QWidget,
)

from ._roi import ROIBase, ROIOrigin
from .qt import ROILayerAccessor, ROITableModel
from .qt.utils import MutableItemModelSequenceWrapper

if TYPE_CHECKING:
    from vispy.app.canvas import MouseEvent


class ROIWidget(QWidget):
    DEFAULT_COLUMN_WIDTHS = (120, 80, 80, 80, 90, 90, 80)
    ROI_LAYER_TEXT_COLOR = "red"

    def __init__(self, napari_viewer: Viewer, parent: Optional[QWidget] = None) -> None:
        super(ROIWidget, self).__init__(parent=parent)
        self._initialized = False

        self._viewer = napari_viewer
        self._roi_layer: Optional[Shapes] = None
        self._roi_layer_accessor: Optional[ROILayerAccessor] = None
        self._roi_table_model: Optional[ROITableModel] = None

        self.setMinimumHeight(200)
        self.setLayout(QGridLayout())

        self._add_widget = QWidget(parent=self)
        add_widget_layout = QFormLayout()
        self._add_widget.setLayout(add_widget_layout)
        add_widget_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow
        )
        self._new_roi_name_line_edit = QLineEdit(parent=self._add_widget)
        self._new_roi_name_line_edit.textChanged.connect(
            self._on_new_roi_name_line_edit_text_changed
        )
        add_widget_layout.addRow("Name:", self._new_roi_name_line_edit)

        self._roi_table_widget = QWidget(parent=self)
        roi_table_widget_layout = QFormLayout()
        self._roi_table_widget.setLayout(roi_table_widget_layout)
        roi_table_widget_layout.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )
        self._roi_table_view = QTableView(parent=self._roi_table_widget)
        self._roi_table_view.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows
        )
        self._roi_table_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._roi_table_view.customContextMenuRequested.connect(
            self._on_roi_table_view_context_menu_requested
        )
        roi_table_widget_layout.addRow(self._roi_table_view)
        self._roi_origin_combo_box = QComboBox(parent=self._roi_table_widget)
        self._roi_origin_combo_box.setFixedWidth(200)
        self._roi_origin_combo_box.addItems(
            (
                str(ROIOrigin.CENTER),
                str(ROIOrigin.TOP_LEFT),
                str(ROIOrigin.TOP_RIGHT),
                str(ROIOrigin.BOTTOM_LEFT),
                str(ROIOrigin.BOTTOM_RIGHT),
            )
        )
        self._roi_origin_combo_box.currentTextChanged.connect(
            self._on_roi_origin_combo_box_current_text_changed
        )
        roi_table_widget_layout.addRow("X/Y origin:", self._roi_origin_combo_box)
        self._save_widget = QWidget(parent=self)
        save_widget_layout = QFormLayout()
        self._save_widget.setLayout(save_widget_layout)

        # Directory input & browser
        self._roi_dir_line_edit = QLineEdit(parent=self._save_widget)
        self._roi_dir_line_edit.textChanged.connect(self._on_roi_dir_line_edit_text_changed)
        self._roi_dir_line_edit.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon),
            QLineEdit.ActionPosition.TrailingPosition,
        ).triggered.connect(self._on_roi_dir_line_edit_browse_action_triggered)
        save_widget_layout.addRow("Directory:", self._roi_dir_line_edit)

        # Prefix input
        self._roi_prefix_line_edit = QLineEdit(parent=self._save_widget)
        self._roi_prefix_line_edit.textChanged.connect(self._on_roi_prefix_line_edit_text_changed)
        save_widget_layout.addRow("Prefix:", self._roi_prefix_line_edit)

        # Optional export checkboxes layout
        checkbox_layout = QGridLayout()
        self._save_rois_json_check_box = QCheckBox("Save ROIs (JSON)", parent=self._save_widget)
        self._save_rois_json_check_box.stateChanged.connect(self._on_save_rois_json_check_box_state_changed)
        checkbox_layout.addWidget(self._save_rois_json_check_box, 0, 0)

        self._save_labels_tiff_check_box = QCheckBox("Save Labels (TIFF)", parent=self._save_widget)
        self._save_labels_tiff_check_box.stateChanged.connect(self._on_save_labels_tiff_check_box_state_changed)
        checkbox_layout.addWidget(self._save_labels_tiff_check_box, 0, 1)
        save_widget_layout.addRow(checkbox_layout)

        # Autosave and Save Button layout
        bottom_save_layout = QGridLayout()
        self._autosave_roi_file_check_box = QCheckBox(
            "Autosave", parent=self._save_widget
        )
        self._autosave_roi_file_check_box.stateChanged.connect(
            self._on_autosave_roi_file_check_box_state_changed
        )
        bottom_save_layout.addWidget(self._autosave_roi_file_check_box, 0, 0)

        self._save_push_button = QPushButton(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton),
            "Save",
            parent=self._save_widget,
        )
        self._save_push_button.clicked.connect(self._on_save_push_button_clicked)
        bottom_save_layout.addWidget(self._save_push_button, 0, 1)
        self._load_push_button = QPushButton(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton),
            "Load ROIs…",
            parent=self._save_widget,
        )
        self._load_push_button.clicked.connect(self._on_load_push_button_clicked)
        bottom_save_layout.addWidget(self._load_push_button, 0, 2)
        save_widget_layout.addRow(bottom_save_layout)

        self._update_layout(False)
        self.installEventFilter(self)

        if isinstance(self._viewer.layers.selection.active, Shapes):
            self._roi_layer = self._viewer.layers.selection.active
        self._on_roi_layer_changed(None)
        self._viewer.layers.selection.events.active.connect(
            self._on_active_layer_changed
        )

        self._initialized = True

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ParentChange:
            parent = self.parent()
            if isinstance(parent, QDockWidget):
                parent.dockLocationChanged.connect(self._on_dock_location_changed)
        return super(ROIWidget, self).eventFilter(obj, event)

    def save_roi_file(self) -> None:
        assert self._roi_layer_accessor is not None
        if not self.roi_dir or not self.roi_prefix.strip():
            return

        out_dir = Path(self.roi_dir)
        prefix = self.roi_prefix.strip()

        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            QMessageBox.warning(self._viewer.window.qt_viewer, "Error", f"Could not create directory:\n{e}")
            return

        # 1. Save measurement table as prefix_measurements.csv
        csv_path = out_dir / f"{prefix}_measurements.csv"
        unit = self._roi_layer_accessor.unit
        df = pd.DataFrame(
            data={
                "Name": [roi.name for roi in self._roi_layer_accessor],
                f"Area ({unit}^2)": [roi.area for roi in self._roi_layer_accessor],
                f"Perimeter ({unit})": [roi.perimeter for roi in self._roi_layer_accessor],
                f"Length ({unit})": [roi.length for roi in self._roi_layer_accessor],
                f"Major_Axis ({unit})": [roi.major_axis for roi in self._roi_layer_accessor],
                f"Minor_Axis ({unit})": [roi.minor_axis for roi in self._roi_layer_accessor],
                "Circularity": [
                    roi.circularity for roi in self._roi_layer_accessor
                ],
            }
        )
        try:
            df.to_csv(csv_path, index=False)
        except Exception as e:
            QMessageBox.warning(self._viewer.window.qt_viewer, "Error", f"Could not save measurements CSV:\n{e}")

        # 2. Optionally save ROI shapes and locations as prefix_rois.json
        if self.save_rois_json:
            json_path = out_dir / f"{prefix}_rois.json"
            rois_data = []
            for i in range(len(self._roi_layer_accessor)):
                item = self._roi_layer_accessor[i]
                rois_data.append({
                    "name": item.name,
                    "shape_type": item.shape_type,
                    "scale": self._roi_layer_accessor.pixel_scale.tolist(),
                    "unit": self._roi_layer_accessor.unit,
                    "data": item.data.tolist(),
                })
            try:
                with open(json_path, "w") as f:
                    json.dump(rois_data, f, indent=2)
            except Exception as e:
                QMessageBox.warning(self._viewer.window.qt_viewer, "Error", f"Could not save ROIs JSON:\n{e}")

        # 3. Optionally save label image as prefix_labels.tiff
        if self.save_labels_tiff and self._roi_layer is not None:
            tiff_path = out_dir / f"{prefix}_labels.tiff"
            try:
                labels = self._roi_layer.to_labels()
                tifffile.imwrite(str(tiff_path), labels)
            except Exception as e:
                QMessageBox.warning(self._viewer.window.qt_viewer, "Error", f"Could not save label image TIFF:\n{e}")

    def load_rois_json(self, json_path: Path) -> None:
        assert self._roi_layer is not None
        assert self._roi_layer_accessor is not None
        layer = self._roi_layer
        accessor = self._roi_layer_accessor
        valid_types = {"rectangle", "ellipse", "line", "path", "polygon"}

        # ---- read and validate everything first, so a bad file changes nothing
        try:
            with open(json_path) as f:
                entries = json.load(f)
            datas, types, names = [], [], []
            taken = {roi.name for roi in accessor}
            for e in entries:
                arr = np.asarray(e["data"], dtype=float)
                shape_type = e["shape_type"]
                if shape_type not in valid_types:
                    raise ValueError(f"Unknown shape type: {shape_type!r}")
                if arr.ndim != 2 or arr.shape[1] != layer.ndim:
                    raise ValueError(
                        f"ROI {e.get('name')!r} has {arr.shape[-1]}D coordinates, "
                        f"but the layer is {layer.ndim}D."
                    )
                # keep names unique: Name -> Name_01, Name_02, ...
                name = str(e.get("name", "")).strip() or "ROI"
                if name in taken:
                    stem = re.sub(r"_\d+$", "", name)
                    n = 1
                    while f"{stem}_{n:02d}" in taken:
                        n += 1
                    name = f"{stem}_{n:02d}"
                taken.add(name)
                datas.append(arr)
                types.append(shape_type)
                names.append(name)
        except Exception as e:
            QMessageBox.warning(
                self._viewer.window.qt_viewer, "Error", f"Could not load ROIs:\n{e}"
            )
            return
        if not datas:
            return

        # ---- add shapes; suppress autosave until names are set
        was_initialized = self._initialized
        self._initialized = False
        try:
            n_before = len(layer.data)
            layer.add(datas, shape_type=types)
            for i, name in enumerate(names):
                accessor[n_before + i].name = name
        finally:
            self._initialized = was_initialized
        self._refresh_roi_table_widget()
        if self._initialized and self.autosave_roi_file:
            self.save_roi_file()

    def get_rois(self) -> MutableSequence[ROIBase]:
        assert self._roi_layer_accessor is not None
        assert self._roi_table_model is not None
        return MutableItemModelSequenceWrapper(
            self._roi_layer_accessor, self._roi_table_model
        )

    def _on_dock_location_changed(self, area: Qt.DockWidgetArea) -> None:
        horizontal = area in (
            Qt.DockWidgetArea.TopDockWidgetArea,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self._update_layout(horizontal)

    def _on_active_layer_changed(self, event: Event) -> None:
        old_roi_layer = self._roi_layer
        if isinstance(event.value, Shapes):
            self._roi_layer = event.value
        else:
            self._roi_layer = None
        self._on_roi_layer_changed(old_roi_layer)

    def _on_roi_layer_changed(self, old_roi_layer: Optional[Shapes]) -> None:
        if old_roi_layer is not None:
            old_roi_layer.events.data.disconnect(self._on_roi_layer_data_changed)
            old_roi_layer.events.scale.disconnect(self._on_roi_layer_data_changed)
            old_roi_layer.events.properties.disconnect(self._on_roi_layer_properties_changed)
            old_roi_layer.mouse_drag_callbacks.remove(self._on_roi_layer_mouse_drag)
        if self._roi_layer is not None:
            self._roi_layer_accessor = ROILayerAccessor(self._roi_layer)
            self._roi_table_model = ROITableModel(self._roi_layer_accessor)
            self._roi_layer.events.data.connect(self._on_roi_layer_data_changed)
            self._roi_layer.events.scale.connect(self._on_roi_layer_data_changed)
            self._roi_layer.events.properties.connect(self._on_roi_layer_properties_changed)
            self._roi_layer.mouse_drag_callbacks.append(self._on_roi_layer_mouse_drag)
            self._roi_layer.text = ROILayerAccessor.ROI_NAME_FEATURES_KEY
            self._roi_layer.text.color = self.ROI_LAYER_TEXT_COLOR  # type: ignore
            self.setEnabled(True)
        else:
            self._roi_layer_accessor = None
            self._roi_table_model = None
            self.setEnabled(False)
        old_roi_table_model = self._roi_table_view.model()
        self._roi_table_view.setModel(self._roi_table_model)
        if old_roi_table_model is None and self._roi_table_model is not None:
            for c in range(0, self._roi_table_model.columnCount()):
                self._roi_table_view.setColumnWidth(c, self.DEFAULT_COLUMN_WIDTHS[c])
            self._roi_table_view.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Interactive
            )
        selection_model = self._roi_table_view.selectionModel()
        if selection_model is not None:
            selection_model.selectionChanged.connect(
                self._on_roi_table_view_selection_changed
            )
        self._refresh_add_widget()
        self._refresh_roi_table_widget()
        self._refresh_save_widget()

    def _on_new_roi_name_line_edit_text_changed(self, text: str) -> None:
        self.new_roi_name = text

    def _on_roi_table_view_selection_changed(
        self, selected: QItemSelection, deselected: QItemSelection
    ) -> None:
        assert self._roi_layer is not None
        row_indices = set(index.row() for index in selected.indexes())
        self._roi_layer.selected_data = row_indices
        self._roi_layer.refresh()

    def _on_roi_table_view_context_menu_requested(self, pos: QPoint) -> None:
        index = self._roi_table_view.indexAt(pos)
        if index.isValid():
            menu = QMenu()
            delete_action = menu.addAction(
                self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton),
                "Delete",
            )
            if menu.exec(self._roi_table_view.mapToGlobal(pos)) == delete_action:
                assert self._roi_layer_accessor is not None
                del self._roi_layer_accessor[index.row()]
                self._refresh_roi_table_widget(row_indices=[index.row()])

    def _on_roi_origin_combo_box_current_text_changed(self, text: str) -> None:
        self.roi_origin = ROIOrigin(text)
        self._refresh_roi_table_widget()
        if self._initialized and self.autosave_roi_file:
            self.save_roi_file()

    def _on_autosave_roi_file_check_box_state_changed(
        self, state: int
    ) -> None:
        self.autosave_roi_file = self._autosave_roi_file_check_box.isChecked()
        self._refresh_save_widget()
        if self._initialized and self.autosave_roi_file:
            self.save_roi_file()

    def _on_save_push_button_clicked(self, checked: bool) -> None:
        self.save_roi_file()
    
    def _on_load_push_button_clicked(self, checked: bool) -> None:
        start_dir = (
            str(self.roi_dir)
            if self.roi_dir and self.roi_dir.is_dir()
            else str(Path.home())
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "Load ROIs (JSON)", start_dir, "ROI files (*.json)"
        )
        if path:
            self.load_rois_json(Path(path))
    
    def _on_roi_dir_line_edit_text_changed(self, text: str) -> None:
        self.roi_dir = Path(text) if text.strip() else None
        self._refresh_save_widget()
        if self._initialized and self.autosave_roi_file:
            self.save_roi_file()

    def _on_roi_prefix_line_edit_text_changed(self, text: str) -> None:
        self.roi_prefix = text
        self._refresh_save_widget()
        if self._initialized and self.autosave_roi_file:
            self.save_roi_file()

    def _on_save_rois_json_check_box_state_changed(self, state: int) -> None:
        self.save_rois_json = self._save_rois_json_check_box.isChecked()
        self._refresh_save_widget()
        if self._initialized and self.autosave_roi_file:
            self.save_roi_file()

    def _on_save_labels_tiff_check_box_state_changed(self, state: int) -> None:
        self.save_labels_tiff = self._save_labels_tiff_check_box.isChecked()
        self._refresh_save_widget()
        if self._initialized and self.autosave_roi_file:
            self.save_roi_file()

    def _on_roi_dir_line_edit_browse_action_triggered(self, checked: bool) -> None:
        current_dir = str(self.roi_dir) if self.roi_dir and self.roi_dir.is_dir() else str(Path.home())
        dir_path = QFileDialog.getExistingDirectory(
            parent=self,
            caption="Select Output Directory",
            directory=current_dir,
        )
        if dir_path:
            self.roi_dir = Path(dir_path)
            self._refresh_save_widget()
            if self._initialized and self.autosave_roi_file:
                self.save_roi_file()

    def _on_roi_layer_data_changed(self, event: Event) -> None:
        self._refresh_roi_table_widget()
        if self._initialized and self.autosave_roi_file:
            self.save_roi_file()

    def _on_roi_layer_properties_changed(self, event: Event) -> None:
        self._refresh_roi_table_widget()
        if self._initialized and self.autosave_roi_file:
            self.save_roi_file()

    def _on_roi_layer_mouse_drag(self, roi_layer: Shapes, event: "MouseEvent"):
        if roi_layer.mode.startswith("add_"):
            self.current_roi_name = self._create_roi_name()
        if roi_layer.mode.startswith("add_") or roi_layer.mode in (
            "select",  # move shape
            "direct",  # move vertex
            "vertex_insert",
            "vertex_remove",
        ):
            yield
            self._refresh_roi_table_widget()
            while event.type == "mouse_move":
                self._refresh_roi_table_widget(row_indices=roi_layer.selected_data)
                yield

    def _update_layout(self, horizontal: bool) -> None:
        layout = self.layout()
        if horizontal:
            layout.addWidget(
                self._add_widget,
                0,
                0,
                alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            )
            layout.addWidget(
                self._save_widget,
                1,
                0,
                alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            )
            layout.addWidget(self._roi_table_widget, 0, 1, 2, 1)
        else:
            layout.addWidget(
                self._add_widget, 0, 0, alignment=Qt.AlignmentFlag.AlignTop
            )
            layout.addWidget(self._roi_table_widget, 1, 0)
            layout.addWidget(
                self._save_widget, 2, 0, alignment=Qt.AlignmentFlag.AlignBottom
            )

    def _refresh_add_widget(self) -> None:
        if self._roi_layer_accessor is not None:
            with QSignalBlocker(self._new_roi_name_line_edit):
                self._new_roi_name_line_edit.setText(self.new_roi_name)

    def _refresh_roi_table_widget(
        self, row_indices: Optional[Sequence[int]] = None
    ) -> None:
        if self._roi_table_model is not None:
            if row_indices is not None:
                self._roi_table_model.refresh_rows(row_indices)
            else:
                self._roi_table_model.reset()
        if self._roi_layer_accessor is not None:
            with QSignalBlocker(self._roi_origin_combo_box):
                self._roi_origin_combo_box.setCurrentText(str(self.roi_origin))

    def _refresh_save_widget(self) -> None:
        is_autosave = self.autosave_roi_file
        self._roi_dir_line_edit.setEnabled(not is_autosave)
        self._roi_prefix_line_edit.setEnabled(not is_autosave)
        self._save_rois_json_check_box.setEnabled(not is_autosave)
        self._save_labels_tiff_check_box.setEnabled(not is_autosave)

        with QSignalBlocker(self._roi_dir_line_edit):
            self._roi_dir_line_edit.setText(str(self.roi_dir) if self.roi_dir is not None else "")
        with QSignalBlocker(self._roi_prefix_line_edit):
            self._roi_prefix_line_edit.setText(self.roi_prefix)
        with QSignalBlocker(self._save_rois_json_check_box):
            self._save_rois_json_check_box.setChecked(self.save_rois_json)
        with QSignalBlocker(self._save_labels_tiff_check_box):
            self._save_labels_tiff_check_box.setChecked(self.save_labels_tiff)

        has_valid_target = self.roi_dir is not None and bool(self.roi_prefix.strip())
        self._autosave_roi_file_check_box.setEnabled(has_valid_target)
        with QSignalBlocker(self._autosave_roi_file_check_box):
            self._autosave_roi_file_check_box.setChecked(self.autosave_roi_file)
        self._save_push_button.setEnabled(has_valid_target and not is_autosave)

    def _create_roi_name(self) -> str:
        assert self.new_roi_name is not None
        desired_roi_name = self.new_roi_name
        assert self._roi_layer_accessor is not None
        existing_roi_names = [roi.name for roi in self._roi_layer_accessor]

        # Name not used yet -> take it as typed
        if desired_roi_name not in existing_roi_names:
            return desired_roi_name

        # If the typed name already ends in _<digits>, count on from that stem
        m = re.fullmatch(r"(?P<stem>.*?)_(?P<num>\d+)", desired_roi_name)
        stem = m.group("stem") if m else desired_roi_name

        regex = re.compile(rf"{re.escape(stem)}_(?P<n>\d+)")
        numbers = [
            int(mm.group("n"))
            for name in existing_roi_names
            if (mm := regex.fullmatch(name))
        ]
        return f"{stem}_{max(numbers, default=0) + 1:02d}"

    @property
    def viewer(self) -> Viewer:
        return self._viewer

    @property
    def roi_layer(self) -> Optional[Shapes]:
        return self._roi_layer

    @property
    def roi_layer_accessor(self) -> Optional[ROILayerAccessor]:
        return self._roi_layer_accessor

    @property
    def roi_table_model(self) -> Optional[ROITableModel]:
        return self._roi_table_model

    @property
    def new_roi_name(self) -> Optional[str]:
        if self._roi_layer_accessor is not None:
            return self._roi_layer_accessor.new_roi_name
        return None

    @new_roi_name.setter
    def new_roi_name(self, new_roi_name: str) -> None:
        assert self._roi_layer_accessor is not None
        self._roi_layer_accessor.new_roi_name = new_roi_name
        self._new_roi_name_line_edit.setText(new_roi_name)

    @property
    def roi_origin(self) -> Optional[ROIOrigin]:
        if self._roi_layer_accessor is not None:
            return self._roi_layer_accessor.roi_origin
        return None

    @roi_origin.setter
    def roi_origin(self, roi_origin: ROIOrigin) -> None:
        assert self._roi_layer_accessor is not None
        self._roi_layer_accessor.roi_origin = roi_origin
        self._roi_origin_combo_box.setCurrentText(str(roi_origin))

    @property
    def autosave_roi_file(self) -> bool:
        if self._roi_layer_accessor is not None:
            return self._roi_layer_accessor.autosave_roi_file
        return False

    @autosave_roi_file.setter
    def autosave_roi_file(self, autosave_roi_file: bool) -> None:
        assert self._roi_layer_accessor is not None
        self._roi_layer_accessor.autosave_roi_file = autosave_roi_file
        self._autosave_roi_file_check_box.setChecked(autosave_roi_file)

    @property
    def current_roi_name(self) -> Optional[str]:
        if self._roi_layer_accessor is not None:
            return self._roi_layer_accessor.current_roi_name
        return None

    @current_roi_name.setter
    def current_roi_name(self, current_roi_name: str) -> None:
        assert self._roi_layer_accessor is not None
        self._roi_layer_accessor.current_roi_name = current_roi_name


    @property
    def roi_dir(self) -> Optional[Path]:
        if self._roi_layer_accessor is not None:
            return self._roi_layer_accessor.roi_dir
        return None

    @roi_dir.setter
    def roi_dir(self, roi_dir: Optional[Path]) -> None:
        assert self._roi_layer_accessor is not None
        self._roi_layer_accessor.roi_dir = roi_dir
        self._roi_dir_line_edit.setText(str(roi_dir) if roi_dir is not None else "")

    @property
    def roi_prefix(self) -> str:
        if self._roi_layer_accessor is not None:
            return self._roi_layer_accessor.roi_prefix
        return ""

    @roi_prefix.setter
    def roi_prefix(self, roi_prefix: str) -> None:
        assert self._roi_layer_accessor is not None
        self._roi_layer_accessor.roi_prefix = roi_prefix
        self._roi_prefix_line_edit.setText(roi_prefix)

    @property
    def save_rois_json(self) -> bool:
        if self._roi_layer_accessor is not None:
            return self._roi_layer_accessor.save_rois_json
        return False

    @save_rois_json.setter
    def save_rois_json(self, save_rois_json: bool) -> None:
        assert self._roi_layer_accessor is not None
        self._roi_layer_accessor.save_rois_json = save_rois_json
        self._save_rois_json_check_box.setChecked(save_rois_json)

    @property
    def save_labels_tiff(self) -> bool:
        if self._roi_layer_accessor is not None:
            return self._roi_layer_accessor.save_labels_tiff
        return False

    @save_labels_tiff.setter
    def save_labels_tiff(self, save_labels_tiff: bool) -> None:
        assert self._roi_layer_accessor is not None
        self._roi_layer_accessor.save_labels_tiff = save_labels_tiff
        self._save_labels_tiff_check_box.setChecked(save_labels_tiff)