document.getElementById("downloadBtn").addEventListener("click", () => {
  const url = document.getElementById("urlInput").value.trim();
  const status = document.getElementById("status");
  if (!/^https?:\/\//i.test(url)) {
    status.textContent = "Enter a valid http(s) URL";
    status.className = "status err";
    status.style.display = "block";
    return;
  }
  chrome.runtime.sendMessage({ action: "download", url }, (response) => {
    status.style.display = "block";
    if (response?.status === "sent") {
      status.textContent = "Sent to TDM";
      status.className = "status ok";
      document.getElementById("urlInput").value = "";
    } else {
      status.textContent = "Failed — start Live Server or register protocol";
      status.className = "status err";
    }
  });
});

document.getElementById("urlInput").addEventListener("keypress", (e) => {
  if (e.key === "Enter") document.getElementById("downloadBtn").click();
});
