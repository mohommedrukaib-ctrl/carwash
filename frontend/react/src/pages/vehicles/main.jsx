import { createRoot } from "react-dom/client";
import VehicleListPage from "./VehicleListPage.jsx";

const rootElement = document.getElementById("vehicle-react-root");
const dataElement = document.getElementById("vehicle-page-data");

if (rootElement && dataElement) {
  try {
    const data = JSON.parse(dataElement.textContent);

    const csrfToken =
      document.querySelector(
        "#vehicle-csrf-form input[name='csrfmiddlewaretoken']"
      )?.value || "";

    createRoot(rootElement).render(
      <VehicleListPage data={data} csrfToken={csrfToken} />
    );
  } catch (error) {
    console.error("Could not load the Vehicles page:", error);

    rootElement.innerHTML = `
      <div class="alert alert-danger">
        The Vehicles page could not be loaded.
      </div>
    `;
  }
}