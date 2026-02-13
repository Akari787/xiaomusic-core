$(function () {
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
    const maxRetry = 8;

    const retryFetch = function () {
      $.get('/api/oauth2/status')
        .done(function (oauthStatus) {
          if ((oauthStatus.login_in_progress || !oauthStatus.cloud_available) && retryCount < maxRetry) {
            retryCount += 1;
            if (qrcodeStatus) {
              qrcodeStatus.textContent = '登录处理中，正在同步设备列表...';
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
          });
        })
        .fail(function () {
          if (retryCount < maxRetry) {
            retryCount += 1;
            setTimeout(retryFetch, 1500);
            return;
          }
          if (qrcodeStatus) {
            qrcodeStatus.textContent = '刷新登录状态失败，请稍后重试';
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
    const protocol = window.location.protocol;
    const hostname = window.location.hostname;
    const baseUrl = `${protocol}//${hostname}`;
    console.log(baseUrl);
    $("#hostname").val(baseUrl);
  });

  $("#auto-port").on("click", () => {
    const port = window.location.port;
    console.log(port);
    $("#public_port").val(port);
  });

  $("#refresh-qrcode").on("click", () => {
    fetchQRCode();
  });

  $("#comfit-qrcode").on("click", () => {
    refreshDevicesAfterOAuth();
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
