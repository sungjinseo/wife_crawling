from selenium import webdriver
from selenium.webdriver.common.keys import Keys


driver = webdriver.Chrome('chromedriver')
driver.get('https://map.naver.com/v5/directions/-/-/-/?c=14139078.9231079,4509745.6215210,15,0,0,0,dh')
#네이버 길찾기 주소

driver.find_element_by_xpath('//*[@id="intro_popup_close"]/span').click()

delay = 3
driver.implicitly_wait(delay) # 3초 기달림 


driver.find_element_by_id('directionStart').send_keys('잠실역')
driver.find_element_by_id("directionStart").send_keys(Keys.RETURN) # 엔터 
try :
    driver.find_element_by_xpath('//*[@id="container"]/div[1]/shrinkable-layout/directions-layout/directions-result/directions-search-list/search-list/search-list-contents/perfect-scrollbar/div/div[1]/div/div/div/search-item-place/div').click()

except :
    driver.find_element_by_id('directionGoal').send_keys('신사역')
    driver.find_element_by_id('directionGoal').send_keys(Keys.RETURN)

try :
 	driver.find_element_by_class_name('btn.btn_direction.active').click()
except:
    print('1')