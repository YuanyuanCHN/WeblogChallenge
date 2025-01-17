
# coding: utf-8

# # 1 Processing & Analytical
# 
# 
# 1. Sessionize the web log by IP. Sessionize = aggregrate all page hits by visitor/IP during a fixed time window. https://en.wikipedia.org/wiki/Session_(web_analytics)
# 
# 2. Determine the average session time
# 
# 3. Determine unique URL visits per session. To clarify, count a hit to a unique URL only once per session.
# 
# 4. Find the most engaged users, ie the IPs with the longest session times

# In[1]:


from pyspark import SparkConf
from pyspark import SparkContext
from pyspark.sql import SQLContext
import sys


# In[2]:


#sc.stop()


# In[3]:


## start a session
conf = SparkConf().setMaster("local").setAppName("WebLog")
sc = SparkContext(conf = conf)
sqlContext = SQLContext(sc)


# In[4]:


## read in file
raw_file = sc.textFile("2015_07_22_mktplace_shop_web_log_sample.log")
## processing raw file to get information needed
web_log = raw_file.map(lambda line: line.split(" "))

#Map RDD to a DF for better performance and convenience
def process(line):
    return [line[0],line[2].split(":")[0],line[12]]

info_rdd = web_log.map(lambda line: process(line))

## create data frame keep information will be used only
df=sqlContext.createDataFrame(info_rdd,['TimeStamp','IP','URL'])

## order by IP and time stamp for better observation
df = df.orderBy(["IP", "TimeStamp"])
df.show()


# ## 1.1 Sessionize web log
# After reading the data, we get the observation that the web log covers the following information on which IP requested visiting what URL at what time.
#     
# Follow the instruction, we can sessionize using time instead of navigation. After investigate the data, we found that one IP will visit multiple URL at multiple time, sometimes there's huge time gap between two visit, which should not be consided as one session. 
# 
# In this case, for one IP if there's no more visit after 15 minutes, the user could be considered as inactive. The next visit should be considered as a start of another session.

# In[5]:


## make sure the data have no duplications
df.count(),df.dropDuplicates().count()


# In[6]:


from pyspark.sql.functions import *
from pyspark.sql.types import *
from pyspark.sql.window import Window


# In[7]:


## change to time data type
df = df.withColumn('TimeStamp', df['TimeStamp'].cast(TimestampType()))

## move the time column by 1 lag to find the last visit for most of the rows
w = Window().partitionBy('IP').orderBy('TimeStamp')
df = df.withColumn("LastTime", lag("TimeStamp", 1).over(w))

## calculate the different between this time stamp with last visit
timeDiff = (unix_timestamp(df.TimeStamp)-unix_timestamp(df.LastTime))
df = df.withColumn("TimeDiff", timeDiff)

## rank within each IP to find the first visit for each IP, and put the time difference to zero to analysis
df =  df.withColumn("rank", dense_rank().over(Window.partitionBy("IP").orderBy("TimeStamp")))
#df = df.withColumn('TimeDiff',when((df.rank > 1), df.TimeDiff).otherwise(0))

df = df.orderBy(['IP','TimeStamp'])
#df.show(50)


# In[8]:


## Analysis the time difference distribution, breaking by 15 minutes looks okay at it's already in long tail
import matplotlib.pyplot as plt
get_ipython().run_line_magic('matplotlib', 'inline')

bins, counts = df.filter((col('TimeDiff') >= 100 ) & (col('TimeDiff') <= 1100 )).select(col('TimeDiff')).rdd.flatMap(lambda x: x).histogram(20)
plt.hist(bins[:-1], bins=bins, weights=counts)


# In[9]:


## breaking by 15 minutes looks okay, change the first row of reach IP to large value over 900 to break to session 
df = df.withColumn('TimeDiff',when((df.rank > 1), df.TimeDiff).otherwise(999))

## each time when time differnece is larger than 15 minutes, give a different session ID
df = df.withColumn('SessionID',when((df.TimeDiff > 15*60), monotonically_increasing_id()).otherwise(None))

## fill the session ID by forward value to assign each row with session ID
df = df.withColumn("SessionID", last('SessionID', True).                   over(Window.partitionBy().orderBy().rowsBetween(-sys.maxsize, 0)))

df = df.orderBy(['IP','TimeStamp'])
#df.show(50)


# ## 1.2 Determine the average session time

# In[10]:


## aggregate first/last visit by each session, and take the time difference
Sess_time_df = df.groupBy(['IP',"SessionID"]).agg(max("TimeStamp").alias("LastVisit"),                                               min("TimeStamp").alias("FirstVisit"))

timeDiff = (unix_timestamp(Sess_time_df.LastVisit)-unix_timestamp(Sess_time_df.FirstVisit))
Sess_time_df = Sess_time_df.withColumn("SessionTime", timeDiff)
#Sess_time_df.show()


# In[11]:


## calculate the average session time 
Sess_time_df.select(avg('SessionTime')).show()


# The Average seession time is around 100 seconds, which 1 minutes and 20 seconds.

# ## 1.3 Determine unique URL visits per session

# In[12]:


## Aggregate the distinct URL by session
URL_df = df.groupBy("SessionID").agg(countDistinct("URL").alias("Unique_URL_cnt"))
#URL_df.show()


# In[13]:


## calculate the average unique URL visit  
URL_df.select(avg('Unique_URL_cnt')).show()


# ## 1.4 Find the most engaged users, ie the IPs with the longest session times

# In[14]:


URL_df.count(),Sess_time_df.count()


# In[15]:


## get table with both session time, and unique url with their IP
Session_URL_df = URL_df.join(Sess_time_df, 'SessionID') # Could also use 'left_outer', the counts are the same
#Session_URL_df.show()


# In[16]:


#the most engaged users with the longest session times
Session_URL_df.sort(col("SessionTime").desc()).limit(10).show()


# In[17]:


#the most engaged users with the most unique URL visit
Session_URL_df.sort(col("Unique_URL_cnt").desc()).limit(10).show()


# The top IP's behavior looks quite abnormal

# # Additional questions for Machine Learning Engineer (MLE) candidates:
# 
# 1. Predict the expected load (requests/second) in the next minute
# 
# 2. Predict the session length for a given IP
# 
# 3. Predict the number of unique URL visits by a given IP
# 

# ## Predict the expected load (requests/second) in the next minute
# Get the load per minutes first

# In[18]:


## form a time stamp with no gap in minute
step = 60

minp, maxp = df.select(min("TimeStamp").cast("long"),                       max("TimeStamp").cast("long")).first()

Reference_df = sqlContext.range(
    int((minp / step) * step), int(((maxp / step) + 1) * step), step
).select(col("id").cast("timestamp").alias("TimeStamp"))

Reference_df = Reference_df.groupby(window(Reference_df['TimeStamp'], "1 minutes").alias("TimeWindow")).min()

## aggregate request by minutes
Request_df = df.groupBy(window(df.TimeStamp, "1 minutes").alias("TimeWindow")).count().alias("request_per_s")

## join with referance data frame to have a time series data with no gap
ts_df = Reference_df.join(Request_df, ["TimeWindow"], "leftouter")
ts_df.show()


# In[19]:


Reference_df.count(),Request_df.count()


# ### Problems and Solutions
# As the time series have too much missing value, which means the data could be a very small sample and it doesn't cover the whole time range. There are many missing time windows. It's not ideal to do time series with impute all the missing value. And as data have too much noise, we consider simple model like moving average first.
# 
# Solution:
# * Aggregate count by seconds first, then aggregate average by minutes. Keep the data point have no less than 5 seconds only (the number 5 can be tune later if there's more time)
# * Keep data point that have more than 10% of the past data point, for example, if we using past 60 minutes' moving average, keep the data that have more than 6 data points (the percentage can be tune later if more time)
# * Try impute data using last data point
# * Use MAE as evaluation method, which is less likely to be influence by extreme values.

# In[20]:


## count request by second
seconds_df = df.groupBy(window(df.TimeStamp, '1 seconds').alias('TimeWindow'))                        .agg(min('TimeStamp').alias('minTimeStamp'),                             count('TimeStamp').alias('request_per_s'))
    
## aggregate average request to minutes
minutes_df = seconds_df.groupBy(window(seconds_df.minTimeStamp, '1 minutes')                          .alias('TimeWindow')).agg(min('minTimeStamp').alias('minTimeStamp'),                                                    avg('request_per_s').alias('request_per_s'),                                                   count('minTimeStamp').alias('data_counts'))

## remove the data point that have less than 5 data_counts for each minute
#print('Before Remove',minutes_df.count())
minutes_df = minutes_df.filter(col('data_counts') >= 5)

#print('After Remove',minutes_df.count())


# In[21]:


## lable these rows
minutes_df =  minutes_df.withColumn("flag",lit(1))


# In[22]:


## join with referance data frame to have a time series data with no gap
ts_df = Reference_df.join(minutes_df, ["TimeWindow"], "leftouter")
#ts_df.show()

## add index for each time window 
w = Window.orderBy("TimeWindow")
ts_df = ts_df.withColumn("index", row_number().over(w))


# ###  Moving average
# Try 60 minutes, 30 minutes, 15 minutes as example, number of minutes can be tune if more time

# In[23]:


for i in [60,30,15,5]:
    w = Window.partitionBy().orderBy("index").rowsBetween(-i, 0)
    ts_df = ts_df.withColumn("cnt_"+str(i),count("data_counts").over(w)).                    withColumn("MA_"+str(i),avg("request_per_s").over(w))

#ts_df.show()


# In[24]:


## keep the rows have enough confidence only
ts_eval_df = ts_df.filter((col('cnt_60')>=6) & (col('cnt_15')>=2) & (col('cnt_30')>=3) & (col('cnt_5')>=1) & (col('flag') == 1)).            select(['request_per_s','MA_60','MA_30','MA_15','MA_5'])


# In[26]:


from pyspark.ml.evaluation import RegressionEvaluator 
## print out the MAE for each prediction methods, as it can make most people have better fit
for col_name in ['MA_60','MA_30','MA_15','MA_5']:
    dt_evaluator = RegressionEvaluator(labelCol="request_per_s", predictionCol=col_name, metricName="mae")
    mae = dt_evaluator.evaluate(ts_eval_df)
    print("MAE for " + col_name + ' is : ', mae )


# ### Try Fill NA with forward value

# In[27]:


## fillin NA 
ts_df = ts_df.withColumn("request_fill",                 last('request_per_s', True).over(Window.partitionBy().orderBy('index').                                                  rowsBetween(-sys.maxsize, 0)))

for i in [30,15,5]:
    w = Window.partitionBy().orderBy("index").rowsBetween(-i, 0)
    ts_df = ts_df.withColumn("MA_Fill_"+str(i),avg("request_fill").over(w))


# In[28]:


## keep the rows have enough confidence only
ts_eval_df2 = ts_df.filter((col('cnt_15') >= 2) & (col('cnt_30') >= 3) & (col('cnt_5') >= 1) & (col('flag') == 1)).                select(['request_per_s','MA_Fill_30','MA_Fill_15','MA_Fill_5'])


# In[29]:


## print out the MAE for each prediction methods, as it can make most people have better fit
for col_name in ['MA_Fill_30','MA_Fill_15','MA_Fill_5']:
    dt_evaluator = RegressionEvaluator(labelCol="request_per_s", predictionCol=col_name, metricName="mae")
    mae = dt_evaluator.evaluate(ts_eval_df2)
    print("MAE for " + col_name + ' is : ', mae )


# ### Make prediction for next minutes
# As moving average with 15 minutes have the best performance above, we use the last 15 minutes to make the prediction
# 

# In[30]:


last_15m_df = ts_df.filter(col('index') >= (1112-14))
## check weather there's more than 2 data points for the last 15 minutes
last_15m_df.show()


# In[31]:


last_15m_df.select(avg('request_per_s').alias("prediction")).show()


# ## Predict the session length for a given IP
# 
# As there's no other information available, we can only use IP, then we can use the mean/median for each IP as the best guess. And use the mean/median for all user as the best guess for IP showed first time. As the distribution is very skewed, it's better to use median.

# In[32]:


## plot the session lengh to check the distribution
bins, counts = Session_URL_df.select(col('SessionTime')).rdd.flatMap(lambda x: x).histogram(15)
plt.hist(bins[:-1], bins=bins, weights=counts)


# In[33]:


# if the given ip has a record in the following table
# the prediction for it's session length is the it's previous session's median
SessTime_byIP_df = Session_URL_df.groupBy("IP").agg(
    expr('percentile_approx(SessionTime, 0.5)').alias('MedianTime_byIP'))
SessTime_byIP_df.show()


# In[34]:


# if the given ip has no record in the table
# the prediction for it's session length is the overall session's average
SessTime_default_df = Session_URL_df.agg(expr('percentile_approx(SessionTime, 0.5)').alias('MedianTime'))
SessTime_default_df.show()


# ### Predict the number of unique URL visits by a given IP
# Same as the question above. As there's no other information available, we can only use IP, then we can use the mean/mode for each IP as the best guess. And use the mean/mode for all user as the best guess for IP showed first time.

# In[36]:


## plot the session lengh to check the distribution
bins, counts = Session_URL_df.select(col('Unique_URL_cnt')).rdd.flatMap(lambda x: x).histogram(40)
plt.hist(bins[:-1], bins=bins, weights=counts)


# In[37]:


# if the given ip has a record in the following table
# the prediction for it's session length is the it's previous session's average
URLcnt_byIP_df = Session_URL_df.groupBy("IP").agg(expr('percentile_approx(Unique_URL_cnt, 0.5)').alias('MedianURLcnt_byIP'))
URLcnt_byIP_df.show()


# In[38]:


URLcnt_default_df = Session_URL_df.agg(expr('percentile_approx(Unique_URL_cnt, 0.5)').alias('MedianURLcnt'))
URLcnt_default_df.show()


# ## Other possible information for the later two problems
# * There's browser verion information in the data, but didn't figure out why each records have multiple. If we can identify the browser version, we can use browser brand and version information in the prediction
# * If there's patten in the URL that can fall into categories like 'home page' and 'item page'. This information can also be used for prediction
