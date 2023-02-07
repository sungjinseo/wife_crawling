var GUIDE_THEME_ID = "11100437"; //서울 in 맵 가이드에서 사용할 테마 아이디(운영)
//var GUIDE_THEME_ID = "11101826"; //코로나
//var GUIDE_THEME_ID = "100483"; //서울 in 맵 가이드에서 사용할 테마 아이디(테스트)
var copy_ti = "";
var copy_pi = "";
var copy_pc = "";
var copy_level = "";
var copy_maptile = "";
var copy_lanType = "KOR";

var contentMarker = "";
var contentLayer = "";
var geojsonLayer;

var copy_lon = "";
var copy_lat = "";
var copy_conts_name = "";
var copy_full_address = "";
var copy_conts_id="";
var copy_theme_id="";
var content_body = "";
var level = 11;
function contentView() {
	console.log("smgis2 - jido.copy - contentView")
	var dataString = "cmd=getNewContentsDetail&key="+M.smgisKey+"&theme_id="+copy_ti+"&conts_id="+encodeURI(copy_pi)+"&lan_type="+copy_lanType; //callback=?&
	gJson("/smgis2/qry/THEME", dataString, function(data){
		if(data.retcode == 1 && data.list.length > 0 && data.list.length <= 10){ //데이터 있을 때
			//view2(data.list[0]);
			//[2022.06.16]   - 지도복사시 PIN 형태의 자료만 표출됨 객체가 다 안보이는 오류 - 기능개발
			//[2022.08.31] pi 파라미터로 최대 10개까지 표시하는 기능 추가
			view_pin(data.list);
			if (data.list.length > 1) {
				$("#optBtn ul li").not(":last").remove();
				M.map.setZoom(level-3);
			}
		}else{
			alert("콘텐츠 아이디를 확인해주세요.");
			window.location.href = 'https://map.seoul.go.kr/smgis2/seoulMap';
		}
	});
}
//var iconURL = "/smgis2/file/imgs/pin_0.png";
//				[2022.06.08]   - 핀 이미지 변경
var iconURL = "/smgis2/file/imgs/pin_copy_20220608.png";
//[2022.06.16]   - 지도복사시 PIN 형태의 자료만 표출됨 객체가 다 안보이는 오류 - 기능개발
function view_pin(body_list) {
	for (var i=0; i < body_list.length; i++) {
	    var body = body_list[i];
	    content_body = body;
	    copy_conts_name = body.cot_conts_name;
	    copy_full_address = body.cot_addr_full_new;
	    copy_conts_id = body.cot_conts_id;
	    copy_theme_id = body.cot_theme_id;
	    
	    var lang = body.cot_conts_lan_type;
	    if(lang == "ENG") {
	        BaseMapChange(M.map, L.Dawul.BASEMAP_GEN_ENG);
	    } else if(lang == "JP") {
	        BaseMapChange(M.map, L.Dawul.BASEMAP_GEN_JAN);
	    } else if(lang == "CHA") {
	        BaseMapChange(M.map, L.Dawul.BASEMAP_GEN_CHNA);
	    }
	    
	    var lng = body.cot_coord_x;
	    var lat = body.cot_coord_y;
	    if(lng == undefined || lng == null || lng == "" || lat == undefined || lat == null || lat == "") {
	        alert("콘텐츠 좌표가 존재하지 않습니다.");
	        window.location.href = 'https://map.seoul.go.kr/smgis2/seoulMap';
	    }
	    copy_lon = lng;
	    copy_lat = lat;
	    
	    contentMarker = new L.Marker(new L.LatLng(lat, lng), {
	        icon: new L.Icon({
	            iconUrl: iconURL, //핀 이미지
	            iconSize: [40, 40],
	            iconAnchor: [20, 40], // 오프셋 (핀의 끝이 좌표로 매칭하기 위해 적용)
	        })
	    }).addTo(M.map);
	    if (body_list.length == 1) {
	    	contentMarker.bindPopup("<h2 style='color:#039'>" + body.cot_conts_name + "</h2>",
	    		{ offset: [0, -35], autoPan: true, closeButton: false }).openPopup();
		} else {
			detailSearchMarkers.push(contentMarker);
			$.extend(contentMarker, {
			    popState: false,
			    imgSize: [40, 40],
			    img: iconURL,
			    imgAnchor: [20, 40],
			    theme_name: body.thm_theme_name,
			    conts_cate_name: body.sub_cate_name,
			    content_name: body.cot_conts_name,
			    idx: detailSearchMarkers.length - 1,
			    cot_coord_type: body.cot_coord_type,
			    theme_id: body.cot_theme_id,
			    conts_id: body.cot_conts_id,
			    cont_lan_type: body.cot_conts_lan_type,
			    cot_img_main_url: body.cot_img_main_url != undefined ? body.cot_img_main_url : "",
			    cot_tel_no: body.cot_tel_no != undefined ? body.cot_tel_no : "",
			    cot_name_01: body.cot_name_01 != undefined ? body.cot_name_01 : "",
			    cot_value_01: body.cot_value_01 != undefined ? body.cot_value_01 : "",
			    cot_extra_data_02: body.cot_extra_data_02 != undefined ? body.cot_extra_data_02 : "",
			    properties: body
			});
			contentMarker.on("click",function(e){ // 아이콘 클릭
				lyrTooltipClose();
				tooltipClose();
				content_body = e.target.properties;
				var list = makeOverlapContentsList(e);
				var popup = L.popup({offset : [ -10, -15 ],autoPan : true,closeButton : false,'className' : 'tooltipTrans'});
				if(list != ""){ //마커가 겹치면
					$("#m_tooltip").html("");
					popup.setLatLng(e.latlng).setContent(list).openOn(M.map);
					$("#tooltip_form").mCustomScrollbar({theme : "minimal-dark", mouseWheel : {axis : "y", scrollAmount : 180}});
				}else{
					if(e.target.idxLyr != undefined){
						detailSearchLayers[e.target.idxLyr].popState = true;
						detailSearchLayers[e.target.idxLyr].setStyle(detailSearchLayers[e.target.idxLyr].highlight_style);
					}
					if(detailSearchMarkers[this.idx].length == undefined) 
						this.setIcon(new L.Icon({iconSize:[40,40],iconUrl:'/smgis2/file/imgs/pin/pin0.png',  iconAnchor:[20,40]}));
					this.popState = true;
					
					setContentTooltip(e.target, e.latlng, popup);
				}
			});
		}
	    M.map.setView(new L.LatLng(lat, lng), level, { animate: false });
	    
	    body.cot_coord_style = typeof(body.cot_coord_style) == "string" ? JSON.parse(body.cot_coord_style) : body.cot_coord_style;
	    var style_options = body.cot_coord_style;
	    
	    if(body.cot_coord_style == undefined || jQuery.isEmptyObject(body.cot_coord_style)) { // 이전 버전
	        style_options = { coordType: 1, color: "#0000ff", pattern: "L", weight: 4, opacity: 1, fillPattern: 0, fillColor: "#00ff00", fillOpacity: 0.7, patternSize: 24 };
	        style_options.coordType = Number(body.cot_coord_type);
	        style_options.color = body.cot_line_color;
	        style_options.pattern = body.cot_line_pattern;
	        style_options.weight = body.cot_line_width;
	        body.cot_coord_style = style_options;
	        
	        var type = "Point";
	        switch(style_options.coordType) {
	            case 3:
	                type = "LineString";
	                break;
	            case 5:
	                type = "Polygon";
	                break;
	            case 4:
	                type = "MultiLineString";
	                break;
	            case 6:
	                type = "MultiPolygon";
	                break;
	            case 8:
	                type = "MultiPoint";
	                break;
	        }
	        //[2022.07.04]   - 일부 컨텐츠 api 호출시  alert 창이 띄워 지는오류 확인
	        //alert(body.old_coord_data);
	        if(body.old_coord_data != null && body.old_coord_data != "") {
	            style_options.gdata = { type: type, coordinates: JSON.parse(body.old_coord_data) };
	        } else {
	            style_options.gdata = { type: type, coordinates: [body.cot_coord_x, body.cot_coord_y] };
	        }
	    } else {
	        style_options = body.cot_coord_style;
	    }
	    
	    body.cot_coord_data = typeof(body.cot_coord_data) == "string" ? JSON.parse(body.cot_coord_data) : body.cot_coord_data;
	    if(style_options.gdata != undefined) { //cot_coord_data 구성을 바꿈  하지만 gdata있을경우엔 먼저 gdata를 활용
	        if(typeof(style_options.gdata) == "string") {
	            body.cot_coord_data = JSON.parse(style_options.gdata);
	        } else {
	            body.cot_coord_data = style_options.gdata;
	        }
	    } else {
	        style_options.gdata = body.cot_coord_data;
	    }
	    //alert(JSON.stringify(style_options));
	    
		content_body.style_options = style_options;
	    if(style_options.coordType == 8) { // 다중 포인트
	        markers = new Array();
	        var data = style_options.gdata.coordinates;
	        for(var i = 0; i < data.length; i++) {
	            var coordData = data[i];
	            try {
	                markers.push(new L.Marker(coordData, {
	                    icon: new L.Icon({
	                        iconUrl: "/smgis2/file/imgs/pin/pin_0.png", //핀 이미지
	                        iconSize: [22, 26],
	                        iconAnchor: [11, 26], // 오프셋 (핀의 끝이 좌표로 매칭하기 위해 적용)
	                    })
	                }).addTo(map));
	            } catch (e) {
	                markers.push(new L.Marker([coordData[1], coordData[0]], {
	                    icon: new L.Icon({
	                        iconUrl: "/smgis2/file/imgs/pin/pin_0.png",
	                        iconSize: [22, 26],
	                        iconAnchor: [11, 26], // 오프셋 (핀의 끝이 좌표로 매칭하기 위해 적용)
	                    })
	                }).addTo(map));
	            }
	        }
	        //map.setView(marker.getLatLng(), 10, {animate : false});
	        M.map.setView(new L.LatLng(lat, lng), level, { animate: false });
	    } else if(style_options.coordType == 1) {
	        //	map.setView(marker.getLatLng(), 10, {animate : false});
	        M.map.setView(new L.LatLng(lat, lng), level, { animate: false });
	    } else { // 다중폴리곤 console.log(JSON.parse(body.geom));
	        geojsonLayer = new L.geoJson(style_options.gdata, { style: style_options }).addTo(M.map);
	        M.map.fitBounds(geojsonLayer.getBounds());
	    }
	}
}

function view2(body){
  content_body = body;
  copy_conts_name = body.cot_conts_name;
  copy_full_address = body.cot_addr_full_new;  
  copy_conts_id = body.cot_conts_id;
  copy_theme_id = body.cot_theme_id;
  
  var lang = body.cot_conts_lan_type;
    if(lang == "ENG"){
      BaseMapChange(M.map, L.Dawul.BASEMAP_GEN_ENG);
    }else if(lang == "JP"){
      BaseMapChange(M.map, L.Dawul.BASEMAP_GEN_JAN);
    }else if(lang == "CHA"){
      BaseMapChange(M.map, L.Dawul.BASEMAP_GEN_CHNA);
    }
    //removeAllData();
    
    var lng = body.cot_coord_x;
    var lat = body.cot_coord_y;
    if(lng==undefined || lng==null || lng=="" || lat==undefined || lat==null || lat==""){
    	alert("콘텐츠 좌표가 존재하지 않습니다.");
    	window.location.href = 'https://map.seoul.go.kr/smgis2/seoulMap';
    }
    copy_lon = lng;
    copy_lat = lat;
    
    contentMarker= new L.Marker(new L.LatLng(lat, lng),{icon: new L.Icon({
        iconUrl: iconURL,   //핀 이미지
        iconSize: [40,40],iconAnchor: [20,40],  // 오프셋 (핀의 끝이 좌표로 매칭하기 위해 적용)
      })}).addTo(M.map);
    contentMarker.bindPopup("<h2 style='color:#039'>「"+body.cot_conts_name+"」</h2>",{offset:[0,-20],autoPan:true,closeButton:false});
    M.map.setView(new L.LatLng(lat, lng),level,{animate : false});
    
    var coord_type = body.cot_coord_type;
    var line_pattern = trim(checkNull(body.cot_line_pattern));
    var line_color = body.cot_line_color;
    var line_width = "4";
    if(body.cot_line_weight!="0") {
    	line_width = body.cot_line_weight;
    }
//    console.log(body.cot_coord_style);
    if(body.cot_coord_style != undefined && body.cot_coord_style.value != undefined) {
	    body.cot_coord_style = JSON.parse(body.cot_coord_style.value);
	    coord_type = body.cot_coord_style.coordType;
	    line_pattern = trim(checkNull(body.cot_coord_style.pattern));//fillPattern;
	    line_color = body.cot_coord_style.color;
	    line_width = body.cot_coord_style.weight;
    }
    
    if(body.cot_coord_style != undefined && body.cot_coord_style.gdata != undefined) {
      var geodata = body.cot_coord_style.gdata;
      if(coord_type==8) { // 다중 포인트
        var markers = new Array();
        var data = geodata.coordinates;
        for(var i=0; i<data.length; i++) {
          var coordData = data[i];
          try {
	          markers.push(new L.Marker([coordData[1], coordData[0]],{icon: new L.Icon({
	        	  iconUrl: iconURL, iconSize: [40,40], iconAnchor: [20,40],  // 오프셋 (핀의 끝이 좌표로 매칭하기 위해 적용)
	          })}).addTo(M.map));
          } catch(e) {
        	  markers.push(new L.Marker(coordData,{icon: new L.Icon({
	              iconUrl: iconURL,iconSize: [40,40], iconAnchor: [20,40],  // 오프셋 (핀의 끝이 좌표로 매칭하기 위해 적용)
	          })}).addTo(M.map));
          }
          markers[markers.length-1].bindPopup("<h2 style='color:#039'>「"+body.cot_conts_name+"」</h2>",{offset:[0,-20],autoPan:true,closeButton:false});
          if(i==0) {
        	  M.map.setView(new L.LatLng(lat, lng),level,{animate : false});
          }
        }        
        var multipoint_layer = L.featureGroup(markers);
        M.map.fitBounds(multipoint_layer.getBounds());
        contentMarker = markers;      
      } else if(coord_type==1) {
       // M.map.setView(contentMarker.getLatLng(), 10, {animate : false});
      } else { // 다중폴리곤 console.log(JSON.parse(body.geom));
        var lineColor = body.cot_coord_style.color;
        var linePattern = trim(checkNull(body.cot_coord_style.pattern));//fillPattern;
        var lineWeight = body.cot_coord_style.weight;
        if(line_color == "" || line_color == null || line_color.substring(0, 1) != "#") line_color = "#0100FF";
        if(linePattern == "" || linePattern == null || linePattern == "L") linePattern = null;
        else if(linePattern == "D") linePattern = "5,9";
        if(lineWeight == "" || lineWeight == 0) lineWeight = 4;
        var _origin_style = {fill : false, weight : lineWeight, color : lineColor, dashArray : linePattern, opacity : 1, fillColor : "#0100FF", fillOpacity : 0}; //폴리곤 스타일
        geojsonLayer = new L.geoJson(geodata, {style : _origin_style}).addTo(M.map);
        M.map.fitBounds(geojsonLayer.getBounds());
      }      
    } else {
      if(coord_type==8 &&  body.cot_coord_data != undefined) { // 다중 포인트
        var markers = new Array();
        var data = JSON.parse(body.cot_coord_data).coordinates;
        for(var i=0; i<data.length; i++) {
          var coordData = data[i];
          markers.push(new L.Marker(new L.LatLng(coordData[1],coordData[0]),{icon: new L.Icon({
              iconUrl: iconURL,   //핀 이미지
              iconSize: [40,40],
              iconAnchor: [20,40],  // 오프셋 (핀의 끝이 좌표로 매칭하기 위해 적용)
            })}).addTo(M.map));
         markers[markers.length-1].bindPopup("<h2 style='color:#039'>「"+body.cot_conts_name+"」</h2>",{offset:[0,-20],autoPan:true,closeButton:false});
         if(i==0) {
            M.map.setView(new L.LatLng(lat, lng),level,{animate : false});
         }
        }
        var multipoint_layer = L.featureGroup(markers);
        M.map.fitBounds(multipoint_layer.getBounds());
        contentMarker = markers;      
      }else if(body.geom != undefined && JSON.parse(body.geom).type != "Point"){ // 다중폴리곤 console.log(JSON.parse(body.geom));
        var lineColor = body.cot_line_color;
        var linePattern =trim(checkNull(body.cot_line_pattern));
        var lineWeight = body.cot_line_weight;
        if(lineColor == "" || lineColor == null || lineColor.substring(0, 1) != "#") lineColor = "#0100FF";
        if(linePattern == "" || linePattern == null || linePattern == "L") linePattern = null;
        else if(linePattern == "D") linePattern = "5,9";
        if(lineWeight == "" || lineWeight == 0) lineWeight = 4;
        var _origin_style = {fill : false, weight : lineWeight, color : lineColor, dashArray : linePattern, opacity : 1, fillColor : "#0100FF", fillOpacity : 0}; //폴리곤 스타일
        geojsonLayer = new L.geoJson(JSON.parse(body.geom), {style : _origin_style}).addTo(M.map);
        M.map.fitBounds(geojsonLayer.getBounds());
      }else{ // 그외의 경우
        //M.map.setView(marker.getLatLng(), 10, {animate : false});
      }
    }
   
}
function view(_content_body) {
	var lng = _content_body.cot_coord_x;  //x좌표
	var lat = _content_body.cot_coord_y;  //y좌표	
	var style_options = _content_body.style_options;
	
	if(style_options.coordType == 8) { // 다중 포인트
        M.map.setView(new L.LatLng(lat, lng), level, { animate: false });
    } else if(style_options.coordType == 1) {
        M.map.setView(new L.LatLng(lat, lng), level, { animate: false });
    } else { // 다중폴리곤
        M.map.fitBounds(geojsonLayer.getBounds(), { animate: false });
    }
	contentMarker.openPopup();
}

var user_id = "";
var lan_type = copy_lanType;
var FLAG_MAIN_MAP = 1;
function opt_detail(){
	console.log("smgis2 -> 상세보기")
	lan_type = copy_lanType;
	if(content_body!=null) { //데이터 있을 때
    roadView(0, content_body); //지도
		jido_contentDetailLayout(params.theme_name,copy_theme_id);
		//contentDetailLayout([content_body], params.theme_name, content_body.conts_cate_name, null, copy_theme_id); // => content_detail_info.jsp
		$("#d_content_form").show();
//		$("#background").show();
//		$("#background").css("z-index", 3000);
	}
}

/* 20170724 콘텐츠 상세정보 추가 - 라사원  */
//콘텐츠 상세정보 레이아웃
function jido_contentDetailLayout(_theme_name,_idx,_theme_id) {
	//콘텐츠 명
	$("#divContentDetail_title").html(content_body.cot_conts_name);
	//콘텐츠 네비
	var _str_navi = _theme_name;
	if(content_body.cot_conts_name != "") _str_navi += " &gt; " + content_body.cot_conts_name;
	$("#divContentDetail_navi").html(_str_navi);
	
	//기본 정보
	var _str_baseInfo = '<dt>'+$("#msg_Basicinfo").val()+'</dt>';
	_str_baseInfo += '<dd><span>'+$("#msg_Themeinfo").val()+' : </span>' + _str_navi + '</dd>'; //기본 정보 > 콘텐츠 네비
	if(content_body.cot_addr_full_new != undefined){
		_str_baseInfo += '<dd><span>'+$("#msg_Address").val()+' : </span><span id="fullAddrs">' + content_body.cot_addr_full_new + '</span>';
		if(lan_type != "KOR"){
			_str_baseInfo += '<button onclick="fn_addrCopy();" class="addrcopyBtn">'+$("#msg_Copy").val()+'</button>';
		}
		_str_baseInfo += '</dd>'; //기본 정보 > 도로명주소
	}	
	if(content_body.cot_tel_no != undefined && content_body.cot_tel_no !="")
		_str_baseInfo += '<dd><span>'+$("#msg_Contact").val()+' : </span><a href="tel:'+content_body.cot_tel_no+'" style="color:#848c8e">' + content_body.cot_tel_no + '</a></dd>'; //기본 정보 > 연락처
	
	//기본 정보 > url
	if(content_body.cot_extra_data_02 != undefined && content_body.cot_extra_data_02 != ""){
		var cot_url = decodeURIComponent(content_body.cot_extra_data_02);
		if(cot_url.indexOf("http") < 0 && cot_url.indexOf("https") < 0)
			cot_url = "https://map.seoul.go.kr"+cot_url; // http없을 경우
		if(cot_url.indexOf("/smgis/ucimgs/") > -1) {
			cot_url = "https://map.seoul.go.kr/smgis2/file/ucimgs/"+cot_url.split("/ucimgs/")[1]; // http없을 경우
		}
			/*[2022.08.18]   - 테마가 선별진료소 혼잡현황일경우 url => 예약하기 */
		if(content_body.cot_theme_id=='11103350'){
			_str_baseInfo += '<dd><span>당일예약 : </span><a href="'+cot_url+'" onclick="javascript:window.open(this.href);return false;" style="color:#848c8e">'+cot_url+'</a></dd>';
		}else{
			_str_baseInfo += '<dd><span>URL : </span><a href="'+cot_url+'" onclick="javascript:window.open(this.href);return false;" style="color:#848c8e">'+cot_url+'</a></dd>';
		}
	}
	
	//기본 정보 > 동영상, 오디오 url
	var _str_urlInfo = "";
	if(content_body.cot_movie_url != "" || content_body.cot_voice_url != ""){ //동영상, 오디오 url 중 1개라도 있으면
		 //기본 정보 > 비디오, 오디오 URL
		if(content_body.cot_movie_url != undefined && content_body.cot_movie_url != ""){
			content_body.cot_movie_url = decodeURIComponent(content_body.cot_movie_url);
			_str_urlInfo += '<li id="divContentDetail_urlVideo" class="btn_K"><a href="javascript:void(0);" onclick="window.open(\'' + content_body.cot_movie_url + '\');">'+$("#msg_Video").val()+'</a></li>';
		}
		if(content_body.cot_voice_url != undefined && content_body.cot_voice_url != ""){
			content_body.cot_voice_url = decodeURIComponent(content_body.cot_voice_url);
			_str_urlInfo += '<li id="divContentDetail_urlAudio" class="btn_L"><a href="javascript:void(0);" onclick="window.open(\'' + content_body.cot_voice_url + '\');">'+$("#msg_Audio").val()+'</a></li>';
		}
	}
	$("#divContentDetail_urlInfo").html(_str_urlInfo);
	$("#divContentDetail_baseInfo").html(_str_baseInfo);
	
//		var theme_info = null;
//		if(_theme_id == GUIDE_THEME_ID){
//			theme_info = GUIDE_THEME_INFO;
//		}else{
//			if(FLAG_MAIN_MAP){ //스마트서울맵
//				theme_info = fn_getThemeInfo(_theme_id);
//			}else{ //테마복사
//				theme_info = theme_body;
//			}			
//		}
//		// 콘텐츠 수정 버튼
//		if(theme_info != undefined) {
//			console.log(theme_info);
//			var thm_theme_type = (theme_info.thm_theme_type == undefined)? theme_info.THM_THEME_TYPE : theme_info.thm_theme_type;
//			console.log(" 콘텐츠 수정 버튼");
//			if(thm_theme_type != "5" ){ // 공공테마일 경우
//				var thm_auth_code = (theme_info.thm_auth_code == undefined)? theme_info.THM_AUTH_CODE : theme_info.thm_auth_code;
//				var check = false;	
//				if(authCodes.length > 0){
//					for(var i=0;i<authCodes.length;i++){
//						if(thm_auth_code == authCodes[i]){
//							check = true;
//							break;
//						}
//					}
//				}else{
//					var check = false;
//					if(sessionAuthCodes != null && sessionAuthCodes != "" && sessionAuthCodes != "null"){  //20200123 테마인증 정보 존재할 경우
//						var _arrCode = sessionAuthCodes.split("|||");
//						for(var i=0; i<_arrCode.length; i++){
//						  if(thm_auth_code == _arrCode[i]){
//								check = true;
//								break;
//							}
//						}
//					}
//				}
//				if(check){
//					$("#divContentDetail_btnModify").css("display","block");
//					$("#divContentDetail_btnModify").attr("onclick","fn_openModifyContDiv("+_theme_id+",\""+content_body.cot_conts_id+"\")");
//				}else{
//					$("#divContentDetail_btnModify").css("display","none");
//					$("#divContentDetail_btnModify").attr("onclick","");
//				}
//				
//			}else{ // 참여테마일 경우
//				if(user_id==content_body.cot_writer){ // 등록자만 수정 가능
//					$("#divContentDetail_btnModify").css("display","block");
//					$("#divContentDetail_btnModify").attr("onclick","fn_openModifyContDiv("+_theme_id+",\""+content_body.cot_conts_id+"\")");
//				}else{
//					$("#divContentDetail_btnModify").css("display","none");
//					$("#divContentDetail_btnModify").attr("onclick","");
//				}
//			}
//		}
	//기본 정보 > 사진 파일
	var _str_imgList = "";
	var _idx_imgList = 0;
	if(content_body.cot_img_main_url != undefined && content_body.cot_img_main_url != ""){
		_str_imgList += '<li><a href="#" onclick="detailImg(' + _idx_imgList + ')"><img class="c_main_img" src="' + content_body.cot_img_main_url + '" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" alt="테마 사진" /></a></li>';
		_idx_imgList++;
	}	
	if(content_body.cot_img_main_url1 != undefined && content_body.cot_img_main_url1 != ""){
		_str_imgList += '<li><a href="#" onclick="detailImg(' + _idx_imgList + ')"><img class="c_main_img" src="' + content_body.cot_img_main_url1 + '" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" alt="테마 사진" /></a></li>';
		_idx_imgList++;
	} 
	if(content_body.cot_img_main_url2 != undefined && content_body.cot_img_main_url2 != ""){
		_str_imgList += '<li><a href="#" onclick="detailImg(' + _idx_imgList + ')"><img class="c_main_img" src="' + content_body.cot_img_main_url2 + '" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" alt="테마 사진" /></a></li>';
		_idx_imgList++;
	}
	if(content_body.cot_img_main_url3 != undefined && content_body.cot_img_main_url3 != ""){
		_str_imgList += '<li><a href="#" onclick="detailImg(' + _idx_imgList + ')"><img class="c_main_img" src="' + content_body.cot_img_main_url3 + '" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" alt="테마 사진" /></a></li>';
		_idx_imgList++;
	}
	if(content_body.cot_img_main_url4 != undefined && content_body.cot_img_main_url4 != ""){
		_str_imgList += '<li><a href="#" onclick="detailImg(' + _idx_imgList + ')"><img class="c_main_img" src="' + content_body.cot_img_main_url4 + '" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" alt="테마 사진" /></a></li>';
		_idx_imgList++;
	} 
	$("#divContentDetail_pic").html(_str_imgList);

	//상세 정보
	var c_names = new Array();
	var c_values = new Array();
	for(var i=0; i<20; i++){
		c_names.push(eval("content_body.cot_name_" + leadingZeros(i + 1, 2)));
		c_values.push(eval("content_body.cot_value_" + leadingZeros(i + 1, 2)));
	}
	
	var _str_detail = "<dt>"+$('#msg_DtInfo').val()+"</dt>";
	for(var i = 0; i < c_names.length; i++){
		var cot_name = c_names[i];
		if(c_values[i] == undefined) c_values[i]= "";
		var cot_value = c_values[i].replace(/\n/g, "<br/>");
		var linkStart = cot_value.indexOf("http://");
		var linksStart = cot_value.indexOf("https://");
		var original = cot_value;
		if(linkStart != -1){
			var linkEnd = cot_value.indexOf(" ", linkStart);
			var result = "";
			if(linkEnd != -1) result = cot_value.substring(linkStart, linkEnd);
			else result = cot_value;
			var replace = "<a onclick='window.open(\"" + result + "\",\"_system\");'>" + result + "</a>";
			original = original.replace(result, replace);
		}else if (linksStart != -1 ) {
			var linkEnd = cot_value.indexOf(" ", linksStart);
			var result = "";
			if(linkEnd != -1) result = cot_value.substring(linksStart, linkEnd);
			else result = cot_value;
			var replace = "<a onclick='window.open(\"" + result + "\",\"_system\");'>" + result + "</a>";
			original = original.replace(result, replace);
		}
		cot_value = original;
		
		// 질문형테마 구분자 변경('|')
		var cotStr = "";
		if(cot_value.indexOf("|") > -1){
			var cotArr = cot_value.split("|");
			
			cotArr.pop();
			for(var j=0;j<cotArr.length;j++){
				if(cotArr[j].search("기타") > -1){ // 기타 처리	 
					if(j+1 == cotArr.length-1){
						cotStr += cotArr[j] + "("+cotArr[j+1]+")";	
						break;
					}else cotStr += cotArr[j];
				}else{
					if(j < cotArr.length-1) cotStr += cotArr[j]+","; 
					else cotStr += cotArr[j];
				}
			}
		}else cotStr += cot_value;
		cot_value = cotStr; 
		
		if(cot_name != null && cot_name != "" && cot_value != ""){ //제목과 내용이 있을 때만 표시
			var cot_names = cot_name.split(";;;;");
			if(cot_names[1] == undefined || cot_names[1] == null) _str_detail += '<dd class="cdi_txt">' + cot_names[0] + '</dd><dd class="cdi_txt2">' + cot_value + '</dd>';
			else _str_detail += '<dd class="cdi_txt">' + cot_names[0]+ '<span>(' + cot_names[1] + ')</span></dd><dd class="cdi_txt2">' + cot_value + '</dd>';
		}
	}
	$("#divContentDetail_detail").html(_str_detail);
	$("#d_content_form .cdi_p_view").prop("tabindex", "-1").focus(); // 포커스 적용
	
	$("#divContentInsert_comment").hide();
	$("#divContentDetail_comment").hide();
	$("#contDetail_jido").hide();
	$("#cdi_btn_close").css("margin-left","110px");
	
	
//	var contsDetailInfo = new ContentDetailDataInfo(content_body);  //컨텐츠 상세 정보 객체
//	var user_id = "";
//	var theme_auth_ids = "";
//	if(FLAG_THEMELIST==0){ //공공테마
//		if(user_id!="" && FLAG_LIST_DEPTH!=4) {
//			var check = false;
//			if(theme_auth_ids.get(_theme_id)=="everyone") {
//				check = true;
//			}else {
//				for(var i=0; i<authCodes.length; i++) {
//					if(authCodes[i]==theme_auth_ids.get(_theme_id)) {
//						check = true;
//						break;
//					}
//				}
//			}
//			if(check) {
//				$("#d_content_update").css("display","block");
//				$("#d_content_update").on("click",function() {
//					contentDetailClose();
//					contentEdit(content_body,_theme_name,_idx);
//					$("#d_content_update").off("click");
//				});
//			}else {
//				$("#d_content_update").css("display","none");
//			}
//		}else {
//			$("#d_content_update").css("display","none");
//		}
//	}else {
//		if(content_body.cot_writer==user_id) {
//			$("#d_content_update").css("display","block");
//			$("#d_content_update").on("click",function() {
//				contentDetailClose();
//				contentEdit(content_body,_theme_name,_idx);
//				$("#d_content_update").off("click");
//			});
//		}else {
//			$("#d_content_update").css("display","none");
//		}
//	}
//	$("#divContentInsert_comment").hide();
//	$("#divContentDetail_comment").hide();
//	$("#contDetail_jido").hide();
//	$("#cdi_btn_close").css("margin-left","110px");
	
//	var obj = document.getElementById("map_contentDetail");
//	var objDoc = obj .contentWindow || obj .contentDocument;
//	objDoc.ret(content_body);
//	
//	var img_list = "";
//	/*<li><span><a href="#">이미지 자리</a></span></li>*/
//	if(contsDetailInfo.c_main_img!="") {
//		img_list += '<li><span><a href="#" onclick="detailImg(0)"><img class="c_main_img" src="'+contsDetailInfo.c_main_img+'" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" /></a></span></li>';
//		//20200214
//		if(contsDetailInfo.c_main_img1!="") {
//			img_list += '<li><span><a href="#" onclick="detailImg(1)"><img class="c_main_img" src="'+contsDetailInfo.c_main_img1+'" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" /></a></span></li>';
//			if(contsDetailInfo.c_main_img2!="") {
//				img_list += '<li><span><a href="#" onclick="detailImg(2)"><img class="c_main_img" src="'+contsDetailInfo.c_main_img2+'" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" /></a></span></li>';
//				if(contsDetailInfo.c_main_img3!="") {
//					img_list += '<li><span><a href="#" onclick="detailImg(3)"><img class="c_main_img" src="'+contsDetailInfo.c_main_img3+'" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" /></a></span></li>';
//					if(contsDetailInfo.c_main_img4!="") {
//						img_list += '<li><span><a href="#" onclick="detailImg(4)"><img class="c_main_img" src="'+contsDetailInfo.c_main_img4+'" onerror="this.src=\'/smgis2/file/imgs/noimg.jpg\'" /></a></span></li>';
//					}
//				}
//			}
//		}
//		$("#divContentDetail_pic").html(img_list);
//		$("#divContentDetail_pic").show();
//	}else {
//		$("#divContentDetail_pic").html("");
//		$("#divContentDetail_pic").hide();
//	}
//	
//	var d_ncode_name = _theme_name;
//
//	if(content_body.SUB_NAME!="" && content_body.COT_CONTS_LAN_TYPE == "KOR") {
//		d_ncode_name += " &gt; "+content_body.SUB_NAME;
//	}else if(content_body.SUB_NAME!="" && content_body.COT_CONTS_LAN_TYPE != "KOR") {
//		d_ncode_name += " &gt; "+content_body.SUB_NAME_ML;
//	}
//
//	var d_addr = "";
//	if(contsDetailInfo.c_newAddr!="") {  //도로명주소 있을 경우
//		d_addr = contsDetailInfo.c_gu+" "+contsDetailInfo.c_newAddr;
//	}else if(contsDetailInfo.c_oldAddr!=""){  //도로명주소 없을 경우(지번주소 나타나게 적용)
//		d_addr = contsDetailInfo.c_oldAddr;
//	}else{
//		$("#dd_addr").css("display","none");
//	}
//	
//	if(contsDetailInfo.c_video_url=="" && contsDetailInfo.c_audio_url=="") {  //비디오 오디오 url 둘다 없을 때
//		$("#d_mediaForm").css("display","none");
//	}else if(contsDetailInfo.c_video_url=="") {  //비디오 url만 없을 때
//		$("#d_mediaForm").css("display","");
//		$("#d_video").css("display","none");
//		$("#d_audio").css("display","");
//	}else if(contsDetailInfo.c_audio_url=="") {  //오디오 url만 없을 때
//		$("#d_mediaForm").css("display","");
//		$("#d_video").css("display","");
//		$("#d_audio").css("display","none");
//	}else {  //비디오 오디오 url 둘다 있을 때
//		$("#d_mediaForm").css("display","");
//		$("#d_video").css("display","");
//		$("#d_audio").css("display","");
//	}
//	
//	$("#d_content_name").html(contsDetailInfo.c_conts_name);
//	$("#d_theme_info").html(d_ncode_name);
//	$("#d_ncode_name").html(d_ncode_name);
//	$("#d_addr").html(d_addr);
//	if(content_body.COT_TEL_NO != ""){
//		if(content_body.COT_CONTS_LAN_TYPE == "KOR") $("#d_tel").html(contsDetailInfo.c_tel);
//		else $("#d_tel").html("+82 "+contsDetailInfo.c_tel);
//	}else{
//		$("#d_tel").html('');
//		$("#dd_cont").css('display','none');
//	}
//	//$("#d_url").html('<a href="'+contsDetailInfo.c_url+'" target="_blank">'+contsDetailInfo.c_url+'</a></span>');
//	
//	//$("#d_url").html('<a href="#" onclick="window.open(\''+contsDetailInfo.c_url+'\',\'_system\');">'+contsDetailInfo.c_url+'</a></span>');
//	if(contsDetailInfo.c_url != ""){
//		var _url = contsDetailInfo.c_url;
//		if(_url.indexOf("http") < 0 && _url.indexOf("https") < 0)  _url = "http://" + _url;
//		$("#d_url").html('<a href="#" onclick="window.open(\''+_url+'\',\'_system\');">'+contsDetailInfo.c_url+'</a></span>');
//		$("#dd_url").show();
//	}else{
//		$("#dd_url").hide();
//	}
//	
//	var de_info = $('#de_info').val();//상세보기
//	var de_video = $('#de_video').val();//동영상
//	var de_audio = $('#de_audio').val();//오디오
//	var de_close = $('#de_close').val();//닫기
//	
//	$("#d_video").html('<button type="button" onclick="window.open(\''+contsDetailInfo.c_video_url+'\');"><span class="video"></span>'+de_video+'</button>');
//	$("#d_audio").html('<button type="button" onclick="window.open(\''+contsDetailInfo.c_audio_url+'\');"><span class="audio"></span>'+de_audio+'</button>');
//	var subInfo_list = "";
//	
//	subInfo_list += '<dt><span>'+de_info+'</span></dt>';
//	for(var i=0; i<contsDetailInfo.c_names.length; i++) {
//		var cot_name= contsDetailInfo.c_names[i];
//		var cot_value= contsDetailInfo.c_values[i];
//		if(cot_name == null) cot_name = "";
//		if(cot_value == null) cot_value = "";
//		cot_value = cot_value.replace(/\n/g,"<br/>");
//		if(cot_value!="") {
//			if(cot_name!="") {
//				var cot_names = cot_name.split(";;;;");
//				subInfo_list += '<dd>';
//				if(cot_names[1]==undefined || cot_names[1]==null) {
//					subInfo_list += '<p class="texts">'+cot_names[0]+'</p>';
//				}else {
//					subInfo_list += '<p class="texts">'+cot_names[0]+'<span>('+cot_names[1]+')</span></p>';
//				}
//				
//				
//				var linkStart = cot_value.indexOf("http://");
//				var original = cot_value;
//				
//				if(linkStart != -1) {
//					var linkEnd = cot_value.indexOf(" ", linkStart);
//					var result = "";
//					
//					if(linkEnd != -1){
//						result = cot_value.substring(linkStart, linkEnd);
//					} else {
//						result = cot_value;
//					}
//					//var replace= "<a href='"+result+"'target='_blank'>"+result+"</a>";
//					var replace= "<a onclick='window.open(\""+result+"\",\"_system\");'>"+result+"</a>";
//					original = original.replace(result, replace);
//				}
//					
//				cot_value = original;
//				
//				// 질문형테마 구분자 변경('!@#$')
//				var cotStr = "";
//				if(cot_value.indexOf("|") > -1){
//					var cotArr = cot_value.split("|");
//					cotArr.pop();
//					for(var j=0;j<cotArr.length;j++){
//						if(cotArr[j].search("기타") > -1){ // 기타 처리	 
//							if(j+1 == cotArr.length-1){
//								cotStr += cotArr[j] + "("+cotArr[j+1]+")";	
//								break;
//							}else{
//								cotStr += cotArr[j];
//							}
//						}else{
//							if(j < cotArr.length-1){
//								cotStr += cotArr[j]+","; 
//							}else{
//								cotStr += cotArr[j];
//							}
//						}
//					}
//				}else{
//					cotStr += cot_value;
//				}
//				
//				
//				subInfo_list += '<p class="texts2">'+cotStr+'</p>';
//				subInfo_list += '</dd>';
//			}
//		}else{ // 내용이 없을때, 제목이 있을 때, 제목에 ';;;;'이 있을 때
//			if (cot_name != "") {
//				var cot_names = cot_name.split(";;;;");
//				
//				
//				if (cot_names[1] == undefined || cot_names[1] == null) {
//					// subInfo_list += '<p class="texts">' + cot_names[0] + '</p>';
//				} else {
//					subInfo_list += '<dd>';
//					subInfo_list += '<p class="texts">' + cot_names[0] + '<span>(' + cot_names[1] + ')</span></p>';
//					subInfo_list += '</dd>';
//				}
//			}
//		}
//	}
//	$("#sub_info").html(subInfo_list);
//	
//	var btn_form = "";
//	btn_form += '<button type="button" onclick="contentDetailClose();">'+de_close+'</button>';
//	
//	$("#d_btn_form").html(btn_form);
}

var ContentDetailDataInfo = function(_content_body) {
	this.c_theme_id = checkNull(_content_body.cot_theme_id);
	this.c_conts_id = checkNull(_content_body.cot_conts_id);
	this.c_conts_name = checkNull(_content_body.cot_conts_name);
	this.c_state = checkNull(_content_body.cot_conts_stat);
	this.c_gu = checkNull(_content_body.cot_gu_name);
	this.c_newAddr = checkNull(_content_body.cot_addr_full_new);
	this.c_oldAddr = checkNull(_content_body.cot_addr_full_old);
	this.c_bzno = checkNull(_content_body.cot_nation_base_area);
	
	this.c_coord_type = checkNull(_content_body.cot_coord_type);
	this.c_lng = checkNull(_content_body.cot_coord_x);
	this.c_lat = checkNull(_content_body.cot_coord_y);
	this.c_color = checkNull(_content_body.cot_line_color);
	this.c_pattern = checkNull(_content_body.cot_line_pattern);
	this.c_width = checkNull(_content_body.cot_line_weight);
	this.c_tel = checkNull(_content_body.cot_tel_no);
	this.c_url = checkNull(_content_body.cot_extra_data_02);
	this.c_video_url = checkNull(_content_body.cot_movie_url);
	this.c_audio_url = checkNull(_content_body.cot_voice_url);
	
	this.c_main_img = checkNull(_content_body.cot_img_main_url);
	this.c_main_img1 = checkNull(_content_body.cot_img_main_url1);
	this.c_main_img2 = checkNull(_content_body.cot_img_main_url2);
	this.c_main_img3 = checkNull(_content_body.cot_img_main_url3);
	this.c_main_img4 = checkNull(_content_body.cot_img_main_url4);
	
	this.c_names = new Array();
	this.c_values = new Array();
	for(var i=0; i<20; i++) {
		var c_name = eval("_content_body.cot_name_"+leadingZeros(i+1,2));
		var c_value = eval("_content_body.cot_value_"+leadingZeros(i+1,2));
		this.c_names.push(c_name);
		this.c_values.push(c_value);
	}
}

/* 20170724 - 길찾기 버튼 추가 - 라사원 */
function opt_road(){
	multiNavi(copy_conts_name,copy_lon,copy_lat);
}

/* 20170724 - 지도보기 버튼 추가 - 라사원 */
function opt_view(){
	M.map.closePopup()
	view(content_body);
}

/* 20170724 - 길찾기 div 표시 - 라사원 */
function multiNavi(name, lng, lat)
{
	// 동적이벤트 등록 시 이벤트 중복 문제
	$(".naver_navi").unbind("click")
	$(".daum_navi").unbind("click")
	$(".tmap_navi").unbind("click")
	
	$("#c_content_navi_form").css("display","block");
	
	if(window.PLANET.os == "etc") 
	{
		$(".selectlist .tmap_navi").css("display","none");
		$(".multinavi .wrap").css("width","200px");
	}
	else
	{
		$(".selectlist .tmap_navi").css("display","block");
		$(".multinavi .wrap").css("width","200px");
	}
	
	// 네이버 지도 - http
	$(".naver_navi").click(function(){
		$("#c_content_navi_form").css("display","none");
		//window.open("http://map.naver.com/index.nhn?slng=&slat=&stext=&elng="+lng+"&elat="+lat+"&etext="+name+"&menu=route&pathType=1","_system");
		
		if(navigator.geolocation){ //GPS를 지원하면
			navigator.geolocation.getCurrentPosition(function(position){
				window.open("http://map.naver.com/index.nhn?slng=" + position.coords.longitude + "&slat=" + position.coords.latitude + "&stext=내 위치&elng="+lng+"&elat="+lat+"&etext="+name+"&menu=route&pathType=1","_system");
			},function(error){
				//console.error(error);
				window.open("http://map.naver.com/index.nhn?slng=&slat=&stext=&elng="+lng+"&elat="+lat+"&etext="+name+"&menu=route&pathType=1","_system");
    	    },{
    	    	enableHighAccuracy: false,
    	    	maximumAge: 0,
    	    	timeout: Infinity
    	    });
		}else{ //gps 지원 X
			window.open("http://map.naver.com/index.nhn?slng=&slat=&stext=&elng="+lng+"&elat="+lat+"&etext="+name+"&menu=route&pathType=1","_system");
		}
	})
	
	// 다음 지도 - http
	$(".daum_navi").click(function(){
		$("#c_content_navi_form").css("display","none")
		name = name.replace(/\,/g, ""); // 콤마 제거
		name = name.replace(/\//g, "|"); // / 제거
		window.open("http://map.daum.net/link/to/"+encodeURIComponent(name)+","+lat+","+lng);
	})
	
	//tmap 지도 - openAPI
		$(".tmap_navi").click(function(){
			$("#c_content_navi_form").css("display","none")
			PLANET.send(copy_full_address);
		})

}

/* 20170724 - 길찾기 div 숨김 - 라사원 */
function unmultiNavi()
{
	$("#c_content_navi_form").css("display","none")
}

/* 20170724 - 길찾기 tmap > os에 따른 연결페이지  - 라사원 */
var PLANET = {};

window.PLANET = window.PLANET || PLANET;

var userAgent = navigator.userAgent.toLocaleLowerCase();
if (userAgent.search("android") > -1) {
    PLANET.os = "android";
} else if (userAgent.search("iphone") > -1 || userAgent.search("ipod") > -1 || userAgent.search("ipad") > -1) {
    PLANET.os = "ios";
} else {
    PLANET.os = "etc";
}

var app = {
    baseUrl : {
        ios : "tmap://",
        android : "tmap://A1",
        etc : "tmap://"
    },
    searchUrl : {
        ios : "tmap://?search=",
        android : "tmap://search?name=",
        etc : "tmap://"
    },
    store : {
        /*android : "http://m.tstore.co.kr/userpoc/mp.jsp?pid=0000163382",*/
    	android:"https://play.google.com/store/apps/details?id=com.skt.tmap.ku&hl=ko",
        ios : "https://itunes.apple.com/app/id431589174",
        etc : "http://www.tmap.co.kr"
    }
};


PLANET.send = function(dest) {
	
    var _app = app;      //앱 목록
    var _os = PLANET.os; //os 종류

    var _baseUrl = _app.baseUrl[_os]; // tmap 구동
    var _searchUrl = app.searchUrl[_os] + dest; // tmap 검색화면 구동

    var _url = (typeof dest == "undefined")? _baseUrl : _searchUrl;

    var startTime = new Date(); 

    if (_os == "ios"){
    	setTimeout(function() {
    		var clickedAt = +new Date;
            if(+new Date - clickedAt < 2000) {
            	window.location.href = _app.store[_os];
            }   
        }, 2000);
        window.location.href = _url;
    }else if(_os == "android") {
        setTimeout(function() {
        	var clickedAt = +new Date;
            if(+new Date - clickedAt < 2000) {
                window.location.href = _app.store[_os];
            }
        }, 2000);
        window.location = _url;
    } else {
    	window.location = _app.store['etc']; //기타 T map 사이트 이동
    }
};

//콘텐츠 상세정보 닫기
function contentDetailClose() {
	$("#d_content_update").off("click");
	$("#d_content_form").css("display","none");
	$("#background").css("display","none");
}

// -- // 라사원

//20200204 로드뷰
function opt_roadView(){
	$("#c_content_navi_form_roadview").css("display","block");
}

function unmultiNavi_roadview(){
	$("#c_content_navi_form_roadview").css("display","none")
}

var CONTENTS_INFO = {'x':0,'y':0};

// 로드뷰
function fn_viewRoad_jido(_type){
	CONTENTS_INFO.x = copy_lon;
	CONTENTS_INFO.y = copy_lat;
	unmultiNavi_roadview();
	fn_viewRoad(_type); // => common.js
}

//주소복사
function adresCopy(address){
	if(address==""){
		address =$("#d_addr").text(); 
	}
	var t = document.createElement("textarea");
	document.body.appendChild(t);
	t.value = address;
	if(PLANET.os == "ios"){
		var editable = t.contentEditable;
		var readOnly = t.readOnly;

		t.contentEditable = true;
		t.readOnly = false;

		var range = document.createRange();
		range.selectNodeContents(t);

		var selection = window.getSelection();
		selection.removeAllRanges();
		selection.addRange(range);

		t.setSelectionRange(0, 999999);
		t.contentEditable = editable;
		t.readOnly = readOnly;

	}else{
		t.select();
	}
	
	document.execCommand('copy');
	document.body.removeChild(t);	
	alert($("#thmCpy_CpAdresMsg").val());
	
}