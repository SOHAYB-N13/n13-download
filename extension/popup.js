document.getElementById("downloadBtn").addEventListener("click", () => {
  const status = document.getElementById("status");
  const urls = document.getElementById("urlInput").value
    .split("\n").map((s) => s.trim()).filter((s) => /^https?:\/\//i.test(s));
  if (!urls.length) {
    status.textContent = "Enter at least one valid http(s) URL";
    status.className = "status err";
    status.style.display = "block";
    return;
  }
  const msg = urls.length === 1 ? { action: "download", url: urls[0] } : { action: "download_many", urls };
  chrome.runtime.sendMessage(msg, (response) => {
    status.style.display = "block";
    if (response?.status === "sent") {
      status.textContent = `Sent ${urls.length} link${urls.length === 1 ? "" : "s"} to TDM`;
      status.className = "status ok";
      document.getElementById("urlInput").value = "";
    } else {
      status.textContent = "Failed — start Live Server or register protocol";
      status.className = "status err";
    }
  });
});

document.getElementById("urlInput").addEventListener("keypress", (e) => {
  if (e.key === "Enter" && !e.shiftKey) document.getElementById("downloadBtn").click();
});
