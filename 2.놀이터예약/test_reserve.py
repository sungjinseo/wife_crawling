import time
import requests
import traceback
from prettytable import PrettyTable

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup as bs
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
import pyperclip


class Playground:
    PLAYGROUND = []
    LOGIN = 'https://www.gangnam.go.kr/login.do?mid=ID91_9101&returnUrl='
    DRIVER = None
    GANGNAM_SESSION = None
    PLAY = 'playground/'
    INSERT = '/insert.do?'
    CANCEL = '/cancel.do?'
    WINDOW = True
    PLAY_TITLE = '놀이터이름'

    def __init__(self, param):
        self.PLAYGROUND = param[:2]
        options = Options()
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_experimental_option('excludeSwitches', ['enable-logging'])
        self.DRIVER = webdriver.Chrome('./chromedriver.exe', chrome_options=options)
        self.DRIVER.implicitly_wait(5)
        self.LOGIN = self.LOGIN + self.PLAYGROUND[0][self.PLAYGROUND[0].index('go.kr') + 5:]
        self.PLAY_TITLE = param[2]
        self.WINDOW = param[3]

    def clipboard_input(self, user_xpath, user_input):
        temp_user_input = pyperclip.paste()  # 사용자 클립보드를 따로 저장
        pyperclip.copy(user_input)
        self.DRIVER.find_element(By.XPATH, user_xpath).click()
        ActionChains(self.DRIVER).key_down(Keys.CONTROL).send_keys('v').key_up(Keys.CONTROL).perform()

        pyperclip.copy(temp_user_input)  # 사용자 클립보드에 저장 된 내용을 다시 가져 옴
        time.sleep(0.5)

    def 로그인(self, login):
        self.DRIVER.get('https://nid.naver.com/nidlogin.login')
        # 아이디와 비밀번호를 입력합니다.
        time.sleep(0.5)  ## 0.5초
        self.clipboard_input('//*[@id="id"]', login['id'])
        self.clipboard_input('//*[@id="pw"]', login['pw'])
        self.DRIVER.find_element(By.XPATH, '//*[@id="log.login"]').click()

        self.DRIVER.get(self.LOGIN)
        self.DRIVER.find_element(By.XPATH,'//*[@id="contents-wrap"]/div[1]/div/div/div/div[1]/div[2]/div/div/ul/li[1]/button').click()

        headers = {
            "User-Agent":
                "Mozilla/5.0 (Windows NT 6.3; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/44.0.2403.157 Safari/537.36"
        }
        ss = requests.session()
        ss.headers.update(headers)

        for cookie in self.DRIVER.get_cookies():
            c = {cookie['name']: cookie['value']}
            ss.cookies.update(c)
        self.GANGNAM_SESSION = ss

        if self.WINDOW != True:
            time.sleep(5)
            self.DRIVER.quit()
        
        return '로그인완료'

    def 예약가능목록(self, play_name=None):

        table = PrettyTable()
        res = None
        if play_name != None:
            res = self.GANGNAM_SESSION.get(play_name[0])
            table.title = play_name[2]

        else:
            res = self.GANGNAM_SESSION.get(self.PLAYGROUND[0])
            table.title = self.PLAY_TITLE

        reserve = bs(res.text, "html.parser")
        table.field_names = ['날짜', '구분', '예약시간_1', '예약번호_1', '예약시간_2', '예약번호_2', '예약시간_3', '예약번호_3']
        for item in reserve.find_all("td"):
            add_item = ['-'] * 8
            add_item[0] = item.select_one('span').get_text() + '일'
            if item.find('div', 'fc-sat') != None or item.find('div', 'holiday-color') != None:
                add_item[1] = '주말'
                # 주말
                for p_tag in item.select('p'):
                    if p_tag.select('a'):
                        link = p_tag.select('a')[0]
                        if '10' in p_tag.get_text().strip():
                            add_item[2] = p_tag.get_text().strip()
                            add_item[3] = link['href'][link['href'].find('playground/') + len('playground/'):link['href'].find('/add.do')]
                        elif '13' in p_tag.get_text().strip():
                            add_item[4] = p_tag.get_text().strip()
                            add_item[5] = link['href'][link['href'].find('playground/') + len('playground/'):link['href'].find('/add.do')]
                        else:
                            add_item[6] = p_tag.get_text().strip()
                            add_item[7] = link['href'][link['href'].find('playground/') + len('playground/'):link['href'].find('/add.do')]
                    else:
                        if p_tag.get_text().strip() != '':
                            if '10' in p_tag.get_text().strip():
                                add_item[2] = p_tag.get_text().strip()
                                add_item[3] = '예약마감'
                            elif '13' in p_tag.get_text().strip():
                                add_item[4] = p_tag.get_text().strip()
                                add_item[5] = '예약마감'
                            else:
                                add_item[6] = p_tag.get_text().strip()
                                add_item[7] = '예약마감'

            else:
                add_item[1] = '평일'
                # 평일
                for p_tag in item.select('p'):
                    if p_tag.select('a'):
                        link = p_tag.select('a')[0]
                        if '13' in p_tag.get_text().strip():
                            add_item[4] = p_tag.get_text().strip()
                            add_item[5] = link['href'][link['href'].find('playground/') + len('playground/'):link['href'].find('/add.do')]
                        else:
                            add_item[6] = p_tag.get_text().strip()
                            add_item[7] = link['href'][link['href'].find('playground/') + len('playground/'):link['href'].find('/add.do')]
                    else:
                        if p_tag.get_text().strip() != '':
                            if '13' in p_tag.get_text().strip():
                                add_item[4] = p_tag.get_text().strip()
                                add_item[5] = '예약마감'
                            else:
                                add_item[6] = '-'
                                add_item[7] = '-'
            if add_item[3].isdigit() or add_item[5].isdigit() or add_item[7].isdigit():
                table.add_row(add_item)

        return table

    def 예약하기(self, reserve_num, info_dict, play_name=None):

        reserve_num = reserve_num.replace(' ','')
        if reserve_num.isdigit() != True and len(reserve_num) != 4:
            return "예약번호는 숫자4자리입니다."

        ## 도곡키값
        ## 보호자인원수 : 2 생년월일 : 3 거주지역 : 6
        ## 세곡키값
        ## 생년월일 : 1, 거주지역 : 2
        ## 일원키값
        ## 몰라아직

        process = True
        result = None
        check_name = self.PLAY_TITLE
        try:
            REQ_STR = []
            if play_name != None:
                REQ_STR.extend(play_name[1][:play_name[1].index(self.PLAY) + len(self.PLAY)] + reserve_num + play_name[1][play_name[1].index(self.INSERT):])
                REQ_STR.extend(play_name[0][play_name[0].index('mid='):])
                check_name = play_name[2]
            else:
                REQ_STR.extend(self.PLAYGROUND[1][:self.PLAYGROUND[1].index(self.PLAY) + len(self.PLAY)] + reserve_num + self.PLAYGROUND[1][self.PLAYGROUND[1].index(self.INSERT):])
                REQ_STR.extend(self.PLAYGROUND[0][self.PLAYGROUND[0].index('mid='):])

            if '도곡놀이터' == check_name:
                # 동반인원
                REQ_STR.extend('&rar_optn_keys=2&rar_optn_values=' + info_dict['rar_optn_value3'])
                # 생년월일
                REQ_STR.extend('&rar_optn_keys=6&rar_optn_values=' + info_dict['rar_optn_value1'])
                # 거주지역
                REQ_STR.extend('&rar_optn_keys=3&rar_optn_values=' + info_dict['rar_optn_value2'])
            else:
                # 생년월일
                REQ_STR.extend('&rar_optn_keys=1&rar_optn_values=' + info_dict['rar_optn_value1'])
                # 거주지역
                REQ_STR.extend('&rar_optn_keys=2&rar_optn_values=' + info_dict['rar_optn_value2'])

            REQ_STR.extend('&rar_user_name=' + info_dict['rar_user_name'] + '&rar_hp_no=' + info_dict['rar_hp_no'] + '&rar_person_cnt=' + info_dict['rar_person_cnt'])
            REQ_STR.extend('&rar_reqst_key=0&rar_file_key=&rar_sms_yn=Y&rar_agree_yn=Y&rar_terms_yn=Y')
            result = self.GANGNAM_SESSION.post(''.join(REQ_STR))
        except:
            traceback.print_stack()
            process = False

        if self.WINDOW == True:
            if play_name != None:
                self.DRIVER.get(play_name[0].replace('apply', 'reqst'))
            else:
                self.DRIVER.get(self.PLAYGROUND[0].replace('apply', 'reqst'))

        if process:
            return '예약요청을 했습니다. 예약결과를 꼭 확인하세요.!!!!!'
        else:
            return '요청중 오류가 발생했습니다.'

    def 예약확인(self, play_name=None):
        table = PrettyTable()
        table.field_names = ['순번', '예약일시', '취소번호', '신청자명', '휴대폰번호', '신청인원', '상태', '등록일']

        res_list = None
        if play_name != None:
            res_list = self.GANGNAM_SESSION.post(play_name[0].replace('apply', 'reqst'))
            table.title = play_name[2]
        else:
            res_list = self.GANGNAM_SESSION.post(self.PLAYGROUND[0].replace('apply', 'reqst'))
            table.title = self.PLAY_TITLE

        confirm_list = bs(res_list.text, "html.parser")
        for item in confirm_list.find_all('tr')[1:]:
            add_item = []
            for detail in item.find_all('td'):
                if len(detail.select('a')) > 0:
                    cancel_str = str(detail.select('a')[0])
                    cancel_str = cancel_str[cancel_str.index('playground/') + 11:cancel_str.index('/view.do')]
                    add_item.append(cancel_str)
                add_item.append(detail.text.strip())
            if len(add_item) == 1:
                add_item = ['-']*8
                add_item[0] = '예약내역이 없습니다'
                table.add_row(add_item)
            else:
                table.add_row(add_item)

        return table

    def 예약취소(self, cancel_num, play_name=None):

        cancel_num = cancel_num.replace(' ','')
        if cancel_num.find('/') > 0:
            if cancel_num[cancel_num.index('/')+1:].isdigit() != True or cancel_num[:cancel_num.index('/')].isdigit() != True:
                return '취소번호를 확인해주세요.'
        else:
            return '취소번호를 확인해주세요.'

        process = True
        result = None
        try:
            REQ_STR = []

            if play_name != None:
                REQ_STR.extend(play_name[1][:play_name[1].index(self.PLAY) + len(self.PLAY)] + cancel_num + self.CANCEL)
                REQ_STR.extend(play_name[0][play_name[0].index('mid='):])
            else:
                REQ_STR.extend(self.PLAYGROUND[1][:self.PLAYGROUND[1].index(self.PLAY) + len(self.PLAY)] + cancel_num + self.CANCEL)
                REQ_STR.extend(self.PLAYGROUND[0][self.PLAYGROUND[0].index('mid='):])

            result = self.GANGNAM_SESSION.post(''.join(REQ_STR))
        except:
            process = False

        if self.WINDOW == True:
            if play_name != None:
                self.DRIVER.get(play_name[0].replace('apply', 'reqst'))
            else:
                self.DRIVER.get(self.PLAYGROUND[0].replace('apply', 'reqst'))

        if process:
            return '취소 처리되었습니다. 처리결과를 꼭 확인하세요~!!!!!'
        else:
            return '취소중 오류가 발생했습니다.'