"""Custom-engraving feature logic (Phase E-2).

Validates an uploaded image, creates a normal Internal work order via the
existing ``WorkOrderService``, records an ``engraving_requests`` row, saves
the upload, and runs generation in a background daemon thread. Generation
results are written back to the record with compare-and-set semantics so a
timed-out or restart-stranded request can never be clobbered by a late
worker.

The vendored generator is heavy (opencv/numpy/matplotlib/trimesh); it is
imported lazily inside the worker so importing this module — and booting
the app — stays light.
"""

import os
import threading

from .repository import STATUS_GENERATING

# Accepted upload extensions / a hard in-memory size ceiling for images.
_ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
_MAX_IMAGE_BYTES = 25 * 1024 * 1024  # 25 MB — images, not gcode


class EngravingValidationError(Exception):
    """A user upload was rejected before any work order was created."""


class EngravingService:
    def __init__(self, *, repository, work_order_service, settings,
                 generate_models=None, render_previews=None, spawn=None):
        self.repository = repository
        self.work_order_service = work_order_service
        self.settings = settings
        # Injectable for tests; default to the lazy vendored imports.
        self._generate_models = generate_models
        self._render_previews = render_previews
        # Injectable spawn strategy (tests run generation inline).
        self._spawn = spawn or self._default_spawn

    # ------------------------------------------------------------------
    # Boot-time reconciliation
    # ------------------------------------------------------------------

    def sweep_stale_generating(self) -> int:
        """Fail any request stranded in ``generating`` by a restart."""
        swept = self.repository.sweep_stale_generating(
            "generation interrupted by a server restart"
        )
        if swept:
            print("[ENGRAVING] swept {} stale generating request(s) "
                  "to failed".format(swept))
        return swept

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    def submit_request(self, *, uploaded_file, product_key, quantity,
                       customer_name) -> dict:
        """Validate, create the WO, store the upload, start generation.

        Returns ``{"wo_id", "engraving_id"}``. Raises
        ``EngravingValidationError`` for any problem detectable *before*
        the work order is created (nothing is persisted in that case).
        A failure *after* WO creation (e.g. the upload cannot be written)
        does not raise — the request row is marked ``failed`` and the
        result is returned so the caller still lands on the created WO.
        """
        product = self._validate_product(product_key)
        customer_name = self._validate_customer(customer_name)
        quantity = self._validate_quantity(quantity)
        image_bytes, ext, original_filename = self._validate_image(
            uploaded_file
        )

        # --- past this point a work order WILL exist ---
        wo_result = self.work_order_service.create_work_order(
            customer_name,
            [],
            due_date=None,
            jobs=[{
                "job_type": "Internal",
                "vendor": None,
                "external_process": None,
                "designer": None,
                "requirements": None,
                "parts": [{
                    "part_name": product["display_name"],
                    "material": product["material"],
                    "quantity": quantity,
                }],
            }],
        )
        wo_id = wo_result["wo_id"]

        engraving_id = self.repository.create(
            wo_id=wo_id, product_key=product_key,
            customer_name=customer_name, quantity=quantity,
            original_filename=original_filename,
        )

        out_dir = os.path.join(self.settings.engraving_dir, str(engraving_id))
        try:
            os.makedirs(out_dir, exist_ok=True)
            upload_path = os.path.join(out_dir, "original" + ext)
            with open(upload_path, "wb") as handle:
                handle.write(image_bytes)
            self.repository.set_upload_path(engraving_id, upload_path)
        except OSError as exc:
            # Terminal-state discipline: same as a generation failure.
            self.repository.mark_failed(
                engraving_id, "failed to store upload: {}".format(exc)
            )
            return {"wo_id": wo_id, "engraving_id": engraving_id}

        self._spawn(engraving_id, upload_path, product_key, out_dir)
        return {"wo_id": wo_id, "engraving_id": engraving_id}

    # ------------------------------------------------------------------
    # Background generation
    # ------------------------------------------------------------------

    def _default_spawn(self, engraving_id, upload_path, product_key, out_dir):
        thread = threading.Thread(
            target=self._run_generation,
            args=(engraving_id, upload_path, product_key, out_dir),
            daemon=True,
        )
        thread.start()

    def _run_generation(self, engraving_id, upload_path, product_key,
                        out_dir) -> None:
        """Run generation under a hard timeout; write the terminal state.

        The actual generation runs in an inner worker thread that we join
        with a timeout. A hung worker cannot be force-killed in Python, so
        on timeout we mark the record failed and abandon the worker; the
        ``WHERE status='generating'`` compare-and-set in the repository
        ensures a late-finishing worker cannot overwrite that terminal
        state.
        """
        timeout = self.settings.engraving_generation_timeout_sec
        holder = {}

        def work():
            try:
                generate_models, render_previews = self._resolve_generators()
                result = generate_models(
                    upload_path, out_dir, product_key=product_key
                )
                previews = render_previews(result, out_dir)
                holder["result"] = (result, previews)
            except Exception as exc:  # noqa: BLE001 - report any failure
                holder["error"] = exc

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(timeout)

        if worker.is_alive():
            self.repository.mark_failed(
                engraving_id,
                "generation timed out after {}s".format(timeout),
            )
            return
        if "error" in holder:
            self.repository.mark_failed(engraving_id, str(holder["error"]))
            return

        result, previews = holder["result"]
        self.repository.mark_ready(
            engraving_id,
            mold_stl_path=result.mold_path,
            prod_stl_path=result.prod_path,
            mold_preview_path=previews["mold"],
            prod_preview_path=previews["prod"],
            mold_triangles=result.triangle_counts["mold"],
            prod_triangles=result.triangle_counts["prod"],
            duration_seconds=result.duration_seconds,
        )

    def _resolve_generators(self):
        gen = self._generate_models
        ren = self._render_previews
        if gen is None or ren is None:
            from app.domains.engraving.vendored import (
                generate_models,
                render_product_previews,
            )
            gen = gen or generate_models
            ren = ren or render_product_previews
        return gen, ren

    # ------------------------------------------------------------------
    # Reads for routes
    # ------------------------------------------------------------------

    def get_wo_engraving_view(self, wo_id: str) -> dict:
        """Serialize the WO's engraving request for the poll endpoint."""
        record = self.repository.get_by_wo(wo_id)
        if not record:
            return None
        eid = record["engraving_id"]
        products = self.settings.engraving_products
        product_cfg = products.get(record["product_key"], {})
        view = {
            "engraving_id": eid,
            "status": record["status"],
            "product_key": record["product_key"],
            "product_display": product_cfg.get(
                "display_name", record["product_key"]
            ),
            "quantity": record["quantity"],
            "original_filename": record["original_filename"],
            "error_message": record["error_message"],
            "created_at": record["created_at"],
            "completed_at": record["completed_at"],
        }
        if record["status"] == "ready":
            view["triangle_counts"] = {
                "mold": record["mold_triangles"],
                "prod": record["prod_triangles"],
            }
            view["duration_seconds"] = record["duration_seconds"]
            view["preview_prod_url"] = self._url(eid, "preview", "prod")
            view["preview_mold_url"] = self._url(eid, "preview", "mold")
            view["stl_prod_url"] = self._url(eid, "stl", "prod")
            view["stl_mold_url"] = self._url(eid, "stl", "mold")
        return view

    @staticmethod
    def _url(engraving_id, category, which):
        return "/api/engraving/{}/{}/{}".format(engraving_id, category, which)

    def get_artifact_path(self, engraving_id: int, category: str,
                          which: str) -> str:
        """Resolve a stored artifact path from the DB record, or None.

        ``which`` must already be validated to {'prod','mold'} and
        ``category`` to {'preview','stl'} by the caller — this never
        constructs a path from those segments, it only selects a
        DB-stored column, so there is no traversal surface.
        """
        record = self.repository.get(engraving_id)
        if not record:
            return None
        column = {
            ("preview", "prod"): "prod_preview_path",
            ("preview", "mold"): "mold_preview_path",
            ("stl", "prod"): "prod_stl_path",
            ("stl", "mold"): "mold_stl_path",
        }.get((category, which))
        if not column:
            return None
        return record.get(column)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_product(self, product_key) -> dict:
        products = self.settings.engraving_products
        product = products.get(product_key)
        if not product:
            raise EngravingValidationError(
                "Unsupported product: {}".format(product_key)
            )
        material = (product.get("material") or "").strip()
        display_name = (product.get("display_name") or "").strip()
        if not material or not display_name:
            raise EngravingValidationError(
                "Product {} is misconfigured (missing material or "
                "display name)".format(product_key)
            )
        return {"material": material, "display_name": display_name}

    @staticmethod
    def _validate_customer(customer_name) -> str:
        customer_name = (customer_name or "").strip()
        if not customer_name:
            raise EngravingValidationError("Customer name is required.")
        return customer_name

    def _validate_quantity(self, quantity) -> int:
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            raise EngravingValidationError("Quantity must be a whole number.")
        low = self.settings.engraving_min_quantity
        high = self.settings.engraving_max_quantity
        if quantity < low or quantity > high:
            raise EngravingValidationError(
                "Quantity must be between {} and {}.".format(low, high)
            )
        return quantity

    def _validate_image(self, uploaded_file):
        """Return (bytes, extension, secure_original_filename)."""
        from werkzeug.utils import secure_filename

        if uploaded_file is None or not getattr(uploaded_file, "filename", ""):
            raise EngravingValidationError("An image file is required.")

        original_filename = secure_filename(
            os.path.basename(uploaded_file.filename)
        )
        if not original_filename:
            raise EngravingValidationError("Invalid image filename.")

        ext = os.path.splitext(original_filename)[1].lower()
        if ext not in _ALLOWED_EXTENSIONS:
            raise EngravingValidationError(
                "Unsupported image type: {}. Use PNG or JPG.".format(
                    ext or "(none)"
                )
            )

        mimetype = getattr(uploaded_file, "mimetype", "") or ""
        if mimetype and not mimetype.startswith("image/"):
            raise EngravingValidationError(
                "Unsupported content type: {}.".format(mimetype)
            )

        image_bytes = uploaded_file.read()
        if not image_bytes:
            raise EngravingValidationError("The uploaded image is empty.")
        if len(image_bytes) > _MAX_IMAGE_BYTES:
            raise EngravingValidationError(
                "Image is too large (max {} MB).".format(
                    _MAX_IMAGE_BYTES // (1024 * 1024)
                )
            )

        self._validate_decodable(image_bytes)
        return image_bytes, ext, original_filename

    def _validate_decodable(self, image_bytes) -> None:
        """Reject anything opencv can't decode, and out-of-bounds dims."""
        import cv2
        import numpy as np

        arr = cv2.imdecode(
            np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_UNCHANGED
        )
        if arr is None:
            raise EngravingValidationError(
                "The file is not a valid image."
            )
        height, width = arr.shape[0], arr.shape[1]
        low = self.settings.engraving_min_dimension_px
        high = self.settings.engraving_max_dimension_px
        if width < low or height < low:
            raise EngravingValidationError(
                "Image is too small ({}x{}); minimum is {}x{}.".format(
                    width, height, low, low
                )
            )
        if width > high or height > high:
            raise EngravingValidationError(
                "Image is too large ({}x{}); maximum is {}x{}.".format(
                    width, height, high, high
                )
            )
