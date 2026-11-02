// Shared by every page: calm arrival as things scroll into view, and small helpers for the API.

export function reveal(root = document) {
  const items = root.querySelectorAll(".reveal, .seam");
  if (!("IntersectionObserver" in window)) {
    items.forEach((item) => item.classList.add("in"));
    return;
  }
  const watcher = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) {
        entry.target.classList.add("in");
        watcher.unobserve(entry.target);
      }
    }
  }, { rootMargin: "0px 0px -12% 0px", threshold: 0.08 });
  items.forEach((item) => watcher.observe(item));
}

export async function api(path, body) {
  const options = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
  const response = await fetch(path, options);
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (!response.ok) {
    const message = (data && data.error) || "Something went wrong. Try again.";
    throw new Error(message);
  }
  return data;
}

export function remember(key, value) {
  try {
    if (value === undefined) return JSON.parse(localStorage.getItem(key) || "null");
    localStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    return null;
  }
  return value;
}

export function buzz(pattern) {
  try {
    if (navigator.vibrate) navigator.vibrate(pattern);
  } catch (error) {
    // Haptics are a nicety. Nothing depends on them.
  }
}

reveal();
