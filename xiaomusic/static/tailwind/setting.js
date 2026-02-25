$(function () {
  // OAuth2 按钮状态（登录/刷新/退出）
  let oauth2LoggedIn = false;

  function fetchOAuth2Status() {
    return $.get('/api/oauth2/status');
  }

  function fetchDetectedBaseUrl() {
    return $.get('/api/v1/detect_base_url');
  }

  function applyDetectedBaseUrl(baseUrl, message) {
    const $status = $('#base-url-detect-status');
    if (!baseUrl) {
      if ($status.length) {
        $status.text(message || '自动检测失败，请手动填写');
      }
      return;
    }
    try {
      const parsed = new URL(baseUrl);
      const hostOnly = `${parsed.protocol}//${parsed.hostname}`;
      const port = parsed.port || (parsed.protocol === 'https:' ? '443' : '80');
      $('#hostname').val(hostOnly);
      $('#public_port').val(port);
      if ($status.length) {
        $status.text((message || '检测到推荐地址') + `：${baseUrl}`);
      }
    } catch (_e) {
      if ($status.length) {
        $status.text('自动检测失败，请手动填写');
      }
    }
  }

  function setOAuth2LoggedIn(loggedIn) {
    oauth2LoggedIn = !!loggedIn;

    const $action = $("#oauth2-action");
    const $text = $("#oauth2-action-text");
    const $logout = $("#oauth2-logout");
    const $qrcodeImage = $("#qrcode-image");
    const $qrcodeStatus = $("#qrcode-status");

    if (!$action.length) return;

    if (oauth2LoggedIn) {
      $action
        .removeClass("border-blue-200 bg-white text-blue-700 hover:bg-blue-50")
        .addClass("bg-blue-600 text-white hover:bg-blue-700 border-transparent");
      if ($text.length) $text.text("已扫码，刷新设备");
      $logout.prop("hidden", false);
      if ($qrcodeStatus.length) $qrcodeStatus.text("已登录，可点击上方按钮刷新设备列表");
      if ($qrcodeImage.length) $qrcodeImage.addClass("qrcode-image-hidden");
    } else {
      $action
        .removeClass("bg-blue-600 text-white hover:bg-blue-700 border-transparent")
        .addClass("border-blue-200 bg-white text-blue-700 hover:bg-blue-50");
      if ($text.length) $text.text("OAuth2 扫码登录");
      $logout.prop("hidden", true);
      if ($qrcodeStatus.length) $qrcodeStatus.text("点击上方按钮获取登录二维码");
      if ($qrcodeImage.length) $qrcodeImage.attr("src", "").addClass("qrcode-image-hidden");
    }
  }

  // Allow global fetchQRCode() to update state
  window.__oauth2SetLoggedIn = setOAuth2LoggedIn;

  function setJellyfinFieldsVisible(visible) {
    const $fields = $("#jellyfin-fields");
    if (!$fields.length) return;
    $fields.prop("hidden", !visible);
    $fields.find("input, button, select, textarea").prop("disabled", !visible);
  }

  // 先按默认值隐藏/显示
  setJellyfinFieldsVisible($("#jellyfin_enabled").val() === "true");

  $("#jellyfin_enabled").on("change", function () {
    setJellyfinFieldsVisible($(this).val() === "true");
  });

  // 拉取版本
  $.get("/getversion", function (data, status) {
    console.log(data, status, data["version"]);
    $("#version").text(`${data.version}`);
  });

  // 遍历所有的select元素，默认选中只有1个选项的
  const autoSelectOne = () => {
    $('select').each(function () {
      // 如果select元素仅有一个option子元素
      if ($(this).children('option').length === 1) {
        // 选中这个option
        $(this).find('option').prop('selected', true);
      }
    });
  };

  function updateCheckbox(selector, mi_did, device_list, authReady) {
    // 清除现有的内容
    $(selector).empty();

    // 将 mi_did 字符串通过逗号分割转换为数组，以便于判断默认选中项
    var selected_dids = mi_did.split(',');

    // 如果 device_list 为空，则提示用户先完成 OAuth2 登录
    if (device_list.length == 0) {
      const loginTips = authReady ? `<div class="login-tips">未发现可用的小爱设备，请确认 OAuth2 Token 是否有效，或在米家 App 重新扫码登录。</div>` : `<div class="login-tips">未发现可用的小爱设备，请先使用下方二维码完成 OAuth2 登录。</div>`;
      $(selector).append(loginTips);
      return;
    }
    $.each(device_list, function (index, device) {
      var did = device.miotDID;
      var hardware = device.hardware;
      var name = device.name;
      // 创建复选框元素
      var checkbox = $('<input>', {
        type: 'checkbox',
        id: did,
        value: `${did}`,
        class: 'custom-checkbox', // 添加样式类
        // 如果mi_did中包含了该did，则默认选中
        checked: selected_dids.indexOf(did) !== -1
      });

      // 创建标签元素
      var label = $('<label>', {
        for: did,
        class: 'checkbox-label', // 添加样式类
        text: `【${hardware} ${did}】${name}` // 设定标签内容
      });

      // 将复选框和标签添加到目标选择器元素中
      $(selector).append(checkbox).append(label);
    });
  }

  function getSelectedDids(containerSelector) {
    var selectedDids = [];

    // 仅选择给定容器中选中的复选框
    $(containerSelector + ' .custom-checkbox:checked').each(function () {
      var did = this.value;
      selectedDids.push(did);
    });

    return selectedDids.join(',');
  }

  function fetchDeviceList(callback) {
    $.get('/getsetting?need_device_list=true', function (data, status) {
      if (typeof callback === 'function') {
        callback(data, status);
      }
    }).fail(function (xhr) {
      alert(
        '获取设备列表失败: ' +
          (xhr.responseJSON && xhr.responseJSON.detail
            ? xhr.responseJSON.detail
            : xhr.statusText)
      );
    });
  }

  function refreshDevicesAfterOAuth() {
    const qrcodeStatus = document.getElementById('qrcode-status');
    let retryCount = 0;
    // long polling login may take up to ~120s
    const maxRetry = 90;

    const retryFetch = function () {
      $.get('/api/oauth2/status')
        .done(function (oauthStatus) {
          if (oauthStatus && oauthStatus.last_error && !oauthStatus.login_in_progress) {
            if (qrcodeStatus) {
              qrcodeStatus.textContent = `登录失败：${oauthStatus.last_error}，请重新获取二维码`;
            }
            return;
          }
          if (!oauthStatus.cloud_available && retryCount < maxRetry) {
            retryCount += 1;
            if (qrcodeStatus) {
              qrcodeStatus.textContent = `登录处理中，请在米家 App 完成确认...（${retryCount}/${maxRetry}）`;
            }
            setTimeout(retryFetch, 1500);
            return;
          }

          fetchDeviceList(function (data) {
            const authReady = !!data.oauth2_token_available;
            updateCheckbox('#mi_did', data.mi_did || '', data.device_list || [], authReady);
            if (qrcodeStatus) {
              qrcodeStatus.textContent = '登录状态已刷新，请勾选设备后保存配置';
            }
            setOAuth2LoggedIn(authReady);
          });
        })
        .fail(function () {
          if (retryCount < maxRetry) {
            retryCount += 1;
            setTimeout(retryFetch, 1500);
            return;
          }
          if (qrcodeStatus) {
            qrcodeStatus.textContent = '刷新登录状态超时：请确认已在米家 App 完成扫码确认，或重新获取二维码';
          }
        });
    };

    retryFetch();
  }

  // 拉取现有配置
  fetchDeviceList(function (data, status) {
    console.log(data, status);
    const authReady = !!data.oauth2_token_available;
    updateCheckbox("#mi_did", data.mi_did, data.device_list, authReady);

    // 初始化显示
    for (const key in data) {
      const $element = $("#" + key);
      if ($element.length) {
        if (data[key] === true) {
          $element.val('true');
        } else if (data[key] === false) {
          $element.val('false');
        } else {
          $element.val(data[key]);
        }
      }
    }

    autoSelectOne();

    // 配置回填后再根据真实值刷新一次
    setJellyfinFieldsVisible($("#jellyfin_enabled").val() === "true");

    setOAuth2LoggedIn(authReady);

    fetchDetectedBaseUrl()
      .done(function (ret) {
        applyDetectedBaseUrl(ret.base_url, ret.message);
      })
      .fail(function () {
        applyDetectedBaseUrl(null, '自动检测失败，请手动填写');
      });
  });

  $("#oauth2-action").on("click", function () {
    const $btn = $(this);
    $btn.prop('disabled', true);
    fetchOAuth2Status()
      .done(function (st) {
        const hasQrShown = !!String($("#qrcode-image").attr("src") || "").trim();
        if (st && st.login_in_progress) {
          if (!hasQrShown) {
            fetchQRCode();
            return;
          }
          refreshDevicesAfterOAuth();
          return;
        }
        // Always request QR when user clicks, so UI has explicit feedback.
        fetchQRCode();
      })
      .fail(function () {
        fetchQRCode();
      })
      .always(function () {
        $btn.prop('disabled', false);
      });
  });

  $("#oauth2-logout").on("click", function () {
    const $btn = $(this);
    const oldText = $btn.text();
    $btn.prop("disabled", true).text("退出中...");
    $.ajax({
      type: "POST",
      url: "/api/oauth2/logout",
      success: function () {
        setOAuth2LoggedIn(false);
        fetchDeviceList(function (data) {
          const authReady = !!data.oauth2_token_available;
          updateCheckbox('#mi_did', data.mi_did || '', data.device_list || [], authReady);
          autoSelectOne();
        });
      },
      error: function (xhr) {
        alert(
          "退出登录失败: " +
            (xhr.responseJSON && xhr.responseJSON.detail
              ? xhr.responseJSON.detail
              : xhr.statusText)
        );
      },
      complete: function () {
        $btn.prop("disabled", false).text(oldText);
      },
    });
  });

  $(".save-button").on("click", () => {
    var setting = $('#setting');
    var inputs = setting.find('input, select, textarea');
    var data = {};
    inputs.each(function () {
      var id = this.id;
      if (id) {
        data[id] = $(this).val();
      }
    });
    var did_list = getSelectedDids("#mi_did");
    data["mi_did"] = did_list;
    console.log(data)

    $.ajax({
      type: "POST",
      url: "/savesetting",
      contentType: "application/json",
      data: JSON.stringify(data),
      success: (msg) => {
        alert(msg);
        location.reload();
      },
      error: (msg) => {
        alert(msg);
      }
    });
  });

  $("#get_music_list").on("click", () => {
    var music_list_url = $("#music_list_url").val();
    console.log("music_list_url", music_list_url);
    var data = {
      url: music_list_url,
    };
    $.ajax({
      type: "POST",
      url: "/downloadjson",
      contentType: "application/json",
      data: JSON.stringify(data),
      success: (res) => {
        if (res.ret == "OK") {
          $("#music_list_json").val(res.content);
        } else {
          console.log(res);
          alert(res.ret);
        }
      },
      error: (res) => {
        console.log(res);
        alert(res);
      }
    });
  });

  $("#refresh_music_tag").on("click", () => {
    $.ajax({
      type: "POST",
      url: "/refreshmusictag",
      contentType: "application/json",
      success: (res) => {
        console.log(res);
        alert(res.ret);
      },
      error: (res) => {
        console.log(res);
        alert(res);
      }
    });
  });

  $("#upload_yt_dlp_cookie").on("click", () => {
    var fileInput = document.getElementById('yt_dlp_cookies_file');
    var file = fileInput.files[0]; // 获取文件对象
    if (file) {
      var formData = new FormData();
      formData.append("file", file);
      $.ajax({
        url: "/uploadytdlpcookie",
        type: "POST",
        data: formData,
        processData: false,
        contentType: false,
        success: function (res) {
          console.log(res);
          alert("上传成功");
        },
        error: function (jqXHR, textStatus, errorThrown) {
          console.log(res);
          alert("上传失败");
        }
      });
    } else {
      alert("请选择一个文件");
    }
  });


  $("#clear_cache").on("click", () => {
    localStorage.clear();
  });
  $("#hostname").on("change", function () {
    const hostname = $(this).val();
    // 检查是否包含端口号（1到5位数字）
    if (hostname.match(/:\d{1,5}$/)) {
      alert("hostname禁止带端口号");
      // 移除端口号
      $(this).val(hostname.replace(/:\d{1,5}$/, ""));
    }
  });


  $("#auto-hostname").on("click", () => {
    fetchDetectedBaseUrl()
      .done(function (ret) {
        applyDetectedBaseUrl(ret.base_url, ret.message);
      })
      .fail(function () {
        alert('自动检测失败，请手动填写');
      });
  });

  $("#auto-port").on("click", () => {
    const port = window.location.port;
    console.log(port);
    $("#public_port").val(port);
  });

  $("#test-reachability").on("click", () => {
    const did = getSelectedDids('#mi_did').split(',')[0] || '';
    if (!did) {
      alert('请先勾选设备');
      return;
    }
    const hostname = String($('#hostname').val() || '').trim();
    const port = String($('#public_port').val() || '').trim();
    const baseUrl = hostname && port ? `${hostname}:${port}` : '';

    $.ajax({
      type: 'POST',
      url: '/api/v1/test_reachability',
      contentType: 'application/json; charset=utf-8',
      data: JSON.stringify({
        speaker_id: did,
        base_url: baseUrl || null,
      }),
      success: (ret) => {
        if (ret.reachable) {
          alert('地址可达');
        } else {
          alert(`地址不可达: ${ret.error_code || 'unknown'}`);
        }
      },
      error: () => {
        alert('可达性测试失败');
      }
    });
  });

  // Toggle masked secrets (API key, tokens)
  $(document).on("click", ".toggle-secret", function () {
    const $wrap = $(this).closest("div");
    const $input = $wrap.find("input").first();
    if (!$input.length) return;
    const cur = $input.attr("type");
    const next = cur === "password" ? "text" : "password";
    $input.attr("type", next);
    $(this).text(next === "password" ? "显示" : "隐藏");
  });

  // 旧按钮已移除，逻辑整合到 #oauth2-action

  $("#sync-jellyfin").on("click", () => {
    const $btn = $("#sync-jellyfin");
    const oldText = $btn.text();
    $btn.prop("disabled", true).text("同步中...");
    $.ajax({
      type: "POST",
      url: "/api/jellyfin/sync",
      success: (res) => {
        alert(
          "同步完成: 歌单 " +
            (res.list_count || 0) +
            " 个, 歌曲 " +
            (res.track_count || 0) +
            " 首"
        );
        location.reload();
      },
      error: (xhr) => {
        alert(
          "同步失败: " +
            (xhr.responseJSON && xhr.responseJSON.detail
              ? xhr.responseJSON.detail
              : xhr.statusText)
        );
      },
      complete: () => {
        $btn.prop("disabled", false).text(oldText);
      },
    });
  });

});

function fetchQRCode() {
  const qrcodeImage = document.getElementById("qrcode-image");
  const qrcodeStatus = document.getElementById("qrcode-status");
  if (!qrcodeImage || !qrcodeStatus) {
    return;
  }

  qrcodeImage.src = "";
  qrcodeImage.classList.add("qrcode-image-hidden");
  qrcodeStatus.textContent = "正在生成二维码...";

  fetch("/api/get_qrcode")
    .then((response) => response.json())
    .then((data) => {
      if (!data.success) {
        qrcodeStatus.textContent = data.message || "二维码生成失败，请稍后重试";
        return;
      }
      if (data.already_logged_in) {
        qrcodeStatus.textContent = data.message || "已登录，无需扫码";
        if (typeof window.__oauth2SetLoggedIn === "function") {
          window.__oauth2SetLoggedIn(true);
        }
        return;
      }

      qrcodeImage.src = data.qrcode_url || "";
      qrcodeImage.classList.remove("qrcode-image-hidden");
      qrcodeStatus.textContent = "请使用米家 App 扫码登录，完成后点击『已扫码，刷新设备』";
    })
    .catch((error) => {
      console.error("获取二维码失败:", error);
      qrcodeStatus.textContent = "网络错误，请检查连接";
    });
}
