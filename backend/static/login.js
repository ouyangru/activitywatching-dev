document.getElementById("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.target.querySelector("button");
  const error = document.getElementById("error");
  button.disabled = true;
  error.textContent = "";
  try {
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: document.getElementById("token").value }),
    });
    if (!response.ok) throw new Error(response.status === 401 ? "令牌不正确，请重新输入。" : "登录失败，请稍后重试。");
    window.location.replace("/mobile");
  } catch (failure) {
    error.textContent = failure.message || "连接失败，请检查网络。";
  } finally {
    button.disabled = false;
  }
});
