import { useEffect, useRef, useState } from "react";

function notify(message, type = "success") {
  if (typeof window.showToast === "function") {
    window.showToast(message, type);
  } else {
    window.alert(message);
  }
}

function makeListUrl(listUrl, search, status, page = null) {
  const url = new URL(listUrl, window.location.origin);
  url.search = "";

  if (search.trim()) {
    url.searchParams.set("q", search.trim());
  }

  if (status) {
    url.searchParams.set("status", status);
  }

  if (page) {
    url.searchParams.set("page", page);
  }

  return `${url.pathname}${url.search}`;
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {
      success: false,
      error: response.redirected
        ? "Your session may have expired. Please sign in again."
        : "The server returned an unexpected response.",
    };
  }
}

export default function VehicleListPage({ data, csrfToken }) {
  const [search, setSearch] = useState(data.search || "");
  const [status, setStatus] = useState(data.status || "active");

  const [selectedVehicle, setSelectedVehicle] = useState(null);
  const [deleteError, setDeleteError] = useState("");
  const [deleting, setDeleting] = useState(false);

  const searchTimer = useRef(null);
  const deleteModalRef = useRef(null);

  useEffect(() => {
    const modal = deleteModalRef.current;

    const resetModal = () => {
      setSelectedVehicle(null);
      setDeleteError("");
      setDeleting(false);
    };

    modal?.addEventListener("hidden.bs.modal", resetModal);

    return () => {
      modal?.removeEventListener("hidden.bs.modal", resetModal);
      window.clearTimeout(searchTimer.current);
    };
  }, []);

  useEffect(() => {
    if (!selectedVehicle || !deleteModalRef.current) {
      return;
    }

    if (!window.bootstrap?.Modal) {
      notify("The dialog could not open. Please refresh the page.", "error");
      return;
    }

    window.bootstrap.Modal
      .getOrCreateInstance(deleteModalRef.current)
      .show();
  }, [selectedVehicle]);

  const goToList = (nextSearch = search, nextStatus = status, page = null) => {
    window.location.assign(
      makeListUrl(data.listUrl, nextSearch, nextStatus, page)
    );
  };

  const handleSearchChange = (event) => {
    const value = event.target.value;
    setSearch(value);

    window.clearTimeout(searchTimer.current);
    searchTimer.current = window.setTimeout(() => {
      goToList(value, status);
    }, 450);
  };

  const handleSearchSubmit = (event) => {
    event.preventDefault();
    window.clearTimeout(searchTimer.current);
    goToList(search, status);
  };

  const handleStatusChange = (event) => {
    const nextStatus = event.target.value;
    setStatus(nextStatus);
    window.clearTimeout(searchTimer.current);
    goToList(search, nextStatus);
  };

  const deleteVehicle = async () => {
    if (!selectedVehicle) {
      return;
    }

    setDeleting(true);
    setDeleteError("");

    try {
      const response = await fetch(selectedVehicle.deleteUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "X-CSRFToken": csrfToken,
          "X-Requested-With": "XMLHttpRequest",
          Accept: "application/json",
        },
      });

      const result = await readJson(response);

      if (!response.ok || !result.success) {
        setDeleteError(result.error || "The vehicle could not be deleted.");
        return;
      }

      window.bootstrap?.Modal
        .getInstance(deleteModalRef.current)
        ?.hide();

      notify(result.message, "success");
      window.setTimeout(() => window.location.reload(), 800);
    } catch (error) {
      console.error(error);
      setDeleteError("Network error. Please try again.");
    } finally {
      setDeleting(false);
    }
  };

  const clearSearchUrl = makeListUrl(data.listUrl, "", "active");
  const vehicles = data.vehicles || [];
  const page = data.pagination;

  return (
    <>
      <div className="aq-panel">
        <div className="aq-panel-head">
          <div className="aq-chips">
            <span className="aq-chip pri">
              <i className="fas fa-car" aria-hidden="true" /> Total{" "}
              <b>{data.totalCount}</b>
            </span>
          </div>

          <div className="d-flex gap-1">
            <a
              href={data.brandListUrl}
              className="btn btn-sm btn-outline-secondary"
            >
              <i className="fas fa-tags me-1" />
              Brands
            </a>

            {data.canCreate && (
              <a href={data.createUrl} className="btn btn-sm btn-primary">
                <i className="fas fa-plus me-1" />
                Add Vehicle
              </a>
            )}
          </div>
        </div>

        <form className="aq-toolbar" onSubmit={handleSearchSubmit}>
          <div className="input-group" style={{ width: 320 }}>
            <span className="input-group-text">
              <i className="fas fa-search" />
            </span>

            <input
              type="text"
              name="q"
              className="form-control"
              placeholder="Reg no, brand, model, customer…"
              value={search}
              onChange={handleSearchChange}
            />

            {search && (
              <a
                href={clearSearchUrl}
                className="btn btn-outline-secondary btn-sm"
                aria-label="Clear search"
              >
                <i className="fas fa-times" />
              </a>
            )}
          </div>

          <select
            name="status"
            className="form-select"
            style={{ width: 130 }}
            value={status}
            onChange={handleStatusChange}
          >
            <option value="all">All Status</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>

          <button type="submit" className="btn btn-outline-primary btn-sm">
            <i className="fas fa-filter me-1" />
            Filter
          </button>
        </form>

        <div className="aq-panel-body flush">
          {vehicles.length > 0 ? (
            <>
              <div className="aq-table-scroll">
                <table className="table table-hover">
                  <thead>
                    <tr>
                      <th>Registration</th>
                      <th>Brand / Model</th>
                      <th>Type</th>
                      <th>Color</th>
                      <th>Fuel</th>
                      <th>Owner</th>
                      <th>Status</th>
                      <th style={{ width: 80 }} />
                    </tr>
                  </thead>

                  <tbody>
                    {vehicles.map((vehicle) => (
                      <tr key={vehicle.id}>
                        <td className="nowrap">
                          <a href={vehicle.detailUrl}>
                            <code
                              style={{ fontWeight: 700, fontSize: 12 }}
                            >
                              {vehicle.registrationNumber}
                            </code>
                          </a>
                        </td>

                        <td className="nowrap">
                          <span className="fw-semibold">
                            {vehicle.brandName}
                          </span>{" "}
                          <span className="text-muted">
                            {vehicle.modelName}
                            {vehicle.year ? ` · ${vehicle.year}` : ""}
                          </span>
                        </td>

                        <td>{vehicle.vehicleType}</td>

                        <td className="nowrap">
                          {vehicle.colorHex && (
                            <span
                              style={{
                                display: "inline-block",
                                width: 9,
                                height: 9,
                                borderRadius: "50%",
                                background: vehicle.colorHex,
                                border:
                                  "1px solid var(--aq-border-strong)",
                                verticalAlign: "middle",
                                marginRight: 4,
                              }}
                            />
                          )}
                          {vehicle.colorName}
                        </td>

                        <td>{vehicle.fuelType}</td>

                        <td>
                          <a
                            href={vehicle.customerUrl}
                            title={vehicle.customerFullName}
                          >
                            {vehicle.customerName}
                          </a>
                          <span
                            className="text-muted d-block"
                            style={{ fontSize: 10 }}
                          >
                            {vehicle.customerPhone}
                          </span>
                        </td>

                        <td>
                          <span className={vehicle.statusClass}>
                            {vehicle.statusDisplay}
                          </span>
                        </td>

                        <td className="nowrap text-end">
                          <a
                            href={vehicle.detailUrl}
                            className="btn btn-sm btn-outline-primary btn-icon"
                            title="View"
                            aria-label={`View ${vehicle.registrationNumber}`}
                          >
                            <i className="fas fa-eye" />
                          </a>

                          {data.canEdit && (
                            <a
                              href={vehicle.editUrl}
                              className="btn btn-sm btn-outline-secondary btn-icon"
                              title="Edit"
                              aria-label={`Edit ${vehicle.registrationNumber}`}
                            >
                              <i className="fas fa-pen" />
                            </a>
                          )}

                          {data.canDelete && (
                            <button
                              type="button"
                              className="btn btn-sm btn-outline-danger btn-icon"
                              title="Delete"
                              aria-label={`Delete ${vehicle.registrationNumber}`}
                              onClick={() => {
                                setDeleteError("");
                                setSelectedVehicle(vehicle);
                              }}
                            >
                              <i className="fas fa-trash" />
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="aq-pagebar">
                <span>
                  {data.totalCount} vehicle
                  {data.totalCount === 1 ? "" : "s"} · Page {page.number}/
                  {page.numPages}
                </span>

                {page.hasOtherPages && (
                  <ul className="pagination pagination-sm mb-0">
                    {page.hasPrevious && (
                      <li className="page-item">
                        <a
                          className="page-link"
                          href={makeListUrl(
                            data.listUrl,
                            search,
                            status,
                            page.previousPage
                          )}
                          aria-label="Previous page"
                        >
                          ‹
                        </a>
                      </li>
                    )}

                    <li className="page-item disabled">
                      <span className="page-link">{page.number}</span>
                    </li>

                    {page.hasNext && (
                      <li className="page-item">
                        <a
                          className="page-link"
                          href={makeListUrl(
                            data.listUrl,
                            search,
                            status,
                            page.nextPage
                          )}
                          aria-label="Next page"
                        >
                          ›
                        </a>
                      </li>
                    )}
                  </ul>
                )}
              </div>
            </>
          ) : (
            <div className="aq-empty">
              <i className="fas fa-car" aria-hidden="true" />
              <h5>No vehicles found</h5>

              {search ? (
                <>
                  <p>
                    No results for <strong>"{search}"</strong>
                  </p>
                  <a
                    href={clearSearchUrl}
                    className="btn btn-sm btn-outline-secondary"
                  >
                    Clear Search
                  </a>
                </>
              ) : (
                data.canCreate && (
                  <a
                    href={data.createUrl}
                    className="btn btn-sm btn-primary"
                  >
                    <i className="fas fa-plus me-1" />
                    Add First Vehicle
                  </a>
                )
              )}
            </div>
          )}
        </div>
      </div>

      <div
        className="modal fade"
        ref={deleteModalRef}
        tabIndex="-1"
        aria-labelledby="deleteVehicleModalTitle"
        aria-hidden="true"
      >
        <div className="modal-dialog modal-sm">
          <div className="modal-content">
            <div className="modal-header">
              <h5
                className="modal-title text-danger"
                id="deleteVehicleModalTitle"
              >
                <i className="fas fa-triangle-exclamation me-2" />
                Confirm Delete
              </h5>
              <button
                type="button"
                className="btn-close"
                data-bs-dismiss="modal"
                aria-label="Close"
                disabled={deleting}
              />
            </div>

            <div className="modal-body">
              <p className="mb-1">
                Delete vehicle{" "}
                <strong>{selectedVehicle?.registrationNumber}</strong>?
              </p>

              <p className="text-muted mb-0" style={{ fontSize: 10.5 }}>
                Moved to Trash. Restorable by Super Admin.
              </p>

              {deleteError && (
                <div className="alert alert-danger mt-3 mb-0" role="alert">
                  {deleteError}
                </div>
              )}
            </div>

            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-sm btn-secondary"
                data-bs-dismiss="modal"
                disabled={deleting}
              >
                Cancel
              </button>

              <button
                type="button"
                className="btn btn-sm btn-danger"
                onClick={deleteVehicle}
                disabled={deleting}
              >
                <i
                  className={`fas ${
                    deleting ? "fa-spinner fa-spin" : "fa-trash"
                  } me-1`}
                />
                {deleting ? "Deleting…" : "Delete"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}